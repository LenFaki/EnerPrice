import sys
from types import ModuleType, SimpleNamespace

import pytest

from custom_components.nl_day_ahead_prices import async_setup


class FakeServices:
    def __init__(self):
        self.registered = {}

    def has_service(self, domain, service):
        return (domain, service) in self.registered

    def async_register(self, domain, service, handler, **kwargs):
        self.registered[(domain, service)] = (handler, kwargs)

    def async_remove(self, domain, service):
        self.registered.pop((domain, service), None)


@pytest.mark.asyncio
async def test_async_setup_delegates_service_registration(monkeypatch):
    calls = []
    fake_services_module = ModuleType("custom_components.nl_day_ahead_prices.services")
    fake_services_module.async_register_services = lambda hass: calls.append(hass)
    monkeypatch.setitem(
        sys.modules,
        "custom_components.nl_day_ahead_prices.services",
        fake_services_module,
    )
    hass = SimpleNamespace(services=FakeServices(), data={})

    assert await async_setup(hass, {}) is True
    assert calls == [hass]


