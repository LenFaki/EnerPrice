"""Country-specific ENTSO-E market zones; end-to-end coverage in test_belgian_market.py."""

import pytest

from custom_components.nl_day_ahead_prices.providers import entsoe_domain


def test_entsoe_bidding_zones_are_country_specific():
    assert entsoe_domain("NL") == "10YNL----------L"
    assert entsoe_domain("BE") == "10YBE----------2"
    assert entsoe_domain("be") == "10YBE----------2"


def test_entsoe_unknown_country_does_not_silently_use_nl():
    with pytest.raises(ValueError, match="Unsupported ENTSO-E bidding zone"):
        entsoe_domain("DE")
