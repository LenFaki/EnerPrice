from datetime import date, datetime, timezone

from custom_components.nl_day_ahead_prices.models import PriceEntry
from custom_components.nl_day_ahead_prices.providers import parse_energy_charts, parse_nord_pool


def test_parse_nord_pool_converts_to_eur_kwh() -> None:
    payload = {
        "multiAreaEntries": [
            {
                "deliveryStart": "2026-07-02T00:00:00+02:00",
                "entryPerArea": {"NL": 101.5},
            }
        ]
    }

    prices = parse_nord_pool(payload, "NL")

    assert prices[0].price == 0.1015


def test_parse_nord_pool_preserves_quarter_hour_prices() -> None:
    payload = {
        "multiAreaEntries": [
            {
                "deliveryStart": "2026-07-02T10:00:00Z",
                "deliveryEnd": "2026-07-02T10:15:00Z",
                "entryPerArea": {"NL": 100},
            },
            {
                "deliveryStart": "2026-07-02T10:15:00Z",
                "deliveryEnd": "2026-07-02T10:30:00Z",
                "entryPerArea": {"NL": 200},
            },
            {
                "deliveryStart": "2026-07-02T10:30:00Z",
                "deliveryEnd": "2026-07-02T10:45:00Z",
                "entryPerArea": {"NL": 300},
            },
            {
                "deliveryStart": "2026-07-02T10:45:00Z",
                "deliveryEnd": "2026-07-02T11:00:00Z",
                "entryPerArea": {"NL": 400},
            },
        ]
    }

    prices = parse_nord_pool(payload, "NL")

    assert len(prices) == 4
    assert [entry.price for entry in prices] == [0.1, 0.2, 0.3, 0.4]


def test_parse_energy_charts_filters_day_and_converts_to_eur_kwh() -> None:
    july_2 = int(datetime(2026, 7, 2, tzinfo=timezone.utc).timestamp())
    july_3 = int(datetime(2026, 7, 3, tzinfo=timezone.utc).timestamp())
    payload = {
        "unix_seconds": [july_2, july_3],
        "price": [50, 75],
    }

    prices = parse_energy_charts(payload, date(2026, 7, 2))

    assert len(prices) == 1
    assert prices[0].price == 0.05


def test_parse_energy_charts_buckets_by_nl_local_day_not_utc_day() -> None:
    # 23:30 UTC on Jan 15 is 00:30 CET on Jan 16 in the Netherlands: it must
    # land in the Jan 16 bucket, not be dropped into (or missing from) Jan 15
    # just because its UTC timestamp still reads Jan 15.
    late_utc = datetime(2026, 1, 15, 23, 30, tzinfo=timezone.utc)
    payload = {"unix_seconds": [int(late_utc.timestamp())], "price": [50]}

    assert parse_energy_charts(payload, date(2026, 1, 16)) == [PriceEntry(late_utc, 0.05)]
    assert parse_energy_charts(payload, date(2026, 1, 15)) == []
