"""NOAA parsing: damage strings, timestamps, and the cleaned DataFrame."""
import math

import pandas as pd
import pytest

from build_database import parse_damage, build_datetime, clean_dataframe


@pytest.mark.parametrize("raw, expected", [
    ("2.10B", 2_100_000_000.0),
    ("10.00K", 10_000.0),
    ("1.5M", 1_500_000.0),
    ("0.00K", 0.0),
    ("75", 75.0),
    ("5h", 500.0),
    ("1T", 1e12),
])
def test_parse_damage_values(raw, expected):
    assert parse_damage(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, "", "   ", float("nan"), "abc", "K"])
def test_parse_damage_missing_is_none(raw):
    assert parse_damage(raw) is None


def test_parse_damage_zero_is_not_missing():
    assert parse_damage("0.00K") == 0.0


def test_build_datetime_basic():
    assert build_datetime(201104, 27, 1453) == pd.Timestamp("2011-04-27 14:53").to_pydatetime()


@pytest.mark.parametrize("args", [("x", 1, 1), (201104, None, 100), (201102, 30, 100)])
def test_build_datetime_invalid_is_none(args):
    assert build_datetime(*args) is None


def test_build_datetime_clamps_bad_time():
    assert build_datetime(201104, 1, 2575).hour == 23


def test_clean_dataframe_survives_missing_columns():
    raw = pd.DataFrame({
        "DAMAGE_PROPERTY": ["2.10B", "", "10.00K"],
        "DAMAGE_CROPS": ["0.00K", None, "1M"],
        "BEGIN_YEARMONTH": [201104, 201105, 195001],
        "BEGIN_DAY": [27, 1, 3],
        "BEGIN_TIME": [1453, 0, 1200],
    })
    out = clean_dataframe(raw)
    assert len(out) == 3
    assert out["damage_property"].iloc[0] == pytest.approx(2.1e9)
    assert math.isnan(out["damage_property"].iloc[1])
    assert out["damage_crops"].iloc[2] == pytest.approx(1e6)
    assert out["begin_datetime"].iloc[0] == pd.Timestamp("2011-04-27 14:53")
