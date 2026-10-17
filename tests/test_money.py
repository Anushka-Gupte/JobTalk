import pytest
from jobtalk.money import format_inr


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "₹0.00"),
        (0.0, "₹0.00"),
        (999, "₹999.00"),
        (999.99, "₹999.99"),
        (1000, "₹1,000.00"),
        (10000, "₹10,000.00"),
        (100000, "₹1,00,000.00"),
        (123456, "₹1,23,456.00"),
        (1234567.5, "₹12,34,567.50"),
        (10000000, "₹1,00,00,000.00"),
        (-150.5, "-₹150.50"),
        (-1234567.89, "-₹12,34,567.89"),
        (-0.5, "-₹0.50"),
    ],
)
def test_format_inr_values(value, expected):
    assert format_inr(value) == expected


def test_format_inr_custom_symbol():
    assert format_inr(1234567.5, symbol="Rs. ") == "Rs. 12,34,567.50"
    assert format_inr(-500, symbol="Rs. ") == "-Rs. 500.00"


def test_format_inr_none():
    assert format_inr(None) == "—"
