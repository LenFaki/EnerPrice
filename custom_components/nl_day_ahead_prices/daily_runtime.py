"""One isolated Daily Advisor process per config entry.

Only plain price/forecast snapshots enter the executor. Home Assistant state,
registries, options and listeners are accessed exclusively on the event loop.
The child interpreter has its own GIL and GC; no HA GC policy is changed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timezone
from functools import partial
from time import monotonic

from .advisor_gc import profile_advisor_gc
from .advisor_process import AdvisorProcess
from .calculations import all_in_entries_for_supplier, export_entries_for_supplier
from .const import CONF_ADVICE_LANGUAGE, CONF_DAILY_DURATION_MINUTES, CONF_FLEXIBLE_LOAD_POWER_W
from .daily_cache import advice_time_bucket
from .models import PriceEntry
from .smart_energy import build_smart_energy_advice
from .supplier_registry import get_supplier_tariff, market_date

_LOGGER = logging.getLogger(__name__)


def calculate_snapshot(snapshot, converted):
    """Profile the worker without changing Home Assistant GC policy."""
    with profile_advisor_gc("Daily Advisor worker", _LOGGER):
        return _calculate_snapshot(snapshot, converted)


def _calculate_snapshot(snapshot, converted):
    """Pure worker: reuse tariff conversions across changing measurements."""
    price_key, raw, profile, tax, vat, forecast, options, now, language, resolution, tariffs = snapshot
    if converted is None or converted[0] != price_key:
        prices = all_in_entries_for_supplier(raw, tax, profile, vat, tariffs_by_date=tariffs)
        utc_prices = [PriceEntry(item.time.astimezone(timezone.utc), item.price) for item in prices]
        exports = export_entries_for_supplier(raw, profile, vat, tariffs_by_date=tariffs)
        converted = (price_key, utc_prices, exports)
    context = {
        "forecast": forecast, "export_prices": converted[2],
        "duration_minutes": options.get(CONF_DAILY_DURATION_MINUTES, 60),
        "resolution_minutes": resolution,
    }
    result = build_smart_energy_advice(
        electricity_price=None, future_prices=converted[1], now=now,
        flexible_load_power_w=options.get(CONF_FLEXIBLE_LOAD_POWER_W),
        language=language, daily_context=context,
    )["daily_advisor"]
    return result, converted


class DailyAdviceRuntime:
    """Coalesce notifications; never calculate a daily plan in a property read."""

    def __init__(self, coordinator, utcnow):
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self._utcnow = utcnow
        self._results = {}
        self._failed = {}
        self._retry_after = 0.0
        self._wanted = {}
        self._worker = AdvisorProcess()
        self._task = None
        self._stopped = False
        # Do not publish an old price/day/forecast plan while a new one is pending.
        self._pending = {
            "state": "unavailable", "best_window": None, "days": [], "advice": [],
            "chart": [], "forecast_quality": "unavailable", "solar_scenario_available": False,
            "solar_diagnostics": ["advice_update_pending"], "summary": "",
        }

    def _language(self, language):
        options = {**self.coordinator.entry.data, **self.coordinator.entry.options}
        language = language or options.get(CONF_ADVICE_LANGUAGE, "auto")
        if language == "auto":
            language = self.hass.config.language
        # The builder supports Dutch and English; keep at most two result slots.
        return "nl" if language.lower().startswith("nl") else "en"

    def _signature(self, language):
        coordinator = self.coordinator
        options = {**coordinator.entry.data, **coordinator.entry.options}
        return (
            id(coordinator.data), coordinator.daily_forecast.data,
            advice_time_bucket(self._utcnow()), repr(options), language,
        )

    def get(self, language=None):
        """Return a prepared result, scheduling at most one shared worker."""
        language = self._language(language)
        signature = self._signature(language)
        record = self._results.get(language)
        if record is not None and record[0] == signature:
            return record[1]
        if not self._stopped and monotonic() >= self._retry_after and self._failed.get(language) != signature:
            self._wanted[language] = signature
            if self._task is None:
                self._task = self.coordinator.entry.async_create_background_task(
                    self.hass, self._run(), "EnerPrice Daily Advisor calculation"
                )
        return self._pending

    async def async_get(self, language=None):
        """Notifications wait for fresh advice rather than sending a placeholder."""
        language = self._language(language)
        result = self.get(language)
        while self._task is not None and not self._stopped:
            await asyncio.shield(self._task)
            result = self.get(language)
        return result

    def _snapshot(self, language):
        from .sensor import _energy_tax, _selected_supplier_profile, _vat

        coordinator = self.coordinator
        options = {**coordinator.entry.data, **coordinator.entry.options}
        data = coordinator.data
        profile = _selected_supplier_profile(coordinator.entry)
        tax, vat = _energy_tax(coordinator.entry), _vat(coordinator.entry)
        # Retain the source object as well as its identity so identities cannot
        # be reused while an executor job still owns the previous snapshot.
        raw = tuple(data.result.prices) if data else ()
        tariffs = {}
        for item in raw:
            day = market_date(item.time)
            if day not in tariffs:
                tariffs[day] = profile
                if profile.key != "custom" and profile.registry_source != "custom":
                    tariffs[day] = get_supplier_tariff(profile.key, day, mode=profile.tariff_registry_mode) or profile
        # Remote registry state belongs to HA, not the spawned interpreter.
        # Send the resolved tariff for each market date, including future dates.
        price_key = (id(data), profile, tax, vat, tariffs)
        resolution = 15 if data and data.result.effective_price_resolution == "quarter_hour" else 60
        return (
            price_key, raw, profile, tax, vat, coordinator.daily_forecast.snapshot(),
            {key: options[key] for key in (CONF_DAILY_DURATION_MINUTES, CONF_FLEXIBLE_LOAD_POWER_W) if key in options},
            self._utcnow(), language, resolution, tariffs,
        ), data

    async def _run(self):
        try:
            while self._wanted and not self._stopped:
                language = next(iter(self._wanted))
                self._wanted.pop(language)
                signature = self._signature(language)
                try:
                    snapshot, source = self._snapshot(language)
                    result = await self.hass.async_add_executor_job(
                        partial(self._worker.calculate, snapshot)
                    )
                except Exception:  # noqa: BLE001 -- background job must report failure and stop retry storms
                    if self._stopped:
                        return
                    _LOGGER.exception("EnerPrice Daily Advisor calculation failed")
                    self._failed[language] = signature
                    self._retry_after = monotonic() + 60
                    self._wanted.clear()
                    continue
                if self._stopped:
                    return
                # A forecast/price/option change during calculation must never
                # publish the superseded result, including after reload/unload.
                if signature != self._signature(language):
                    self._wanted[language] = self._signature(language)
                    continue
                self._results[language] = (signature, result, source)
                self._failed.pop(language, None)
                self._wanted.pop(language, None)
                self.coordinator.async_update_listeners()
        finally:
            self._task = None

    async def async_stop(self):
        self._stopped = True
        self._wanted.clear()
        # Termination runs in the I/O executor and also releases a blocked recv.
        await self.hass.async_add_executor_job(self._worker.close)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._results.clear()
        self._failed.clear()
