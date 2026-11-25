"""Automatic chart/map selection."""
import pandas as pd

from visuals import choose_visual, render_chart_png


def test_empty_or_none():
    assert choose_visual(None)[0] == "none"
    assert choose_visual(pd.DataFrame())[0] == "none"


def test_single_number_is_none():
    assert choose_visual(pd.DataFrame({"n": [110]}))[0] == "none"


def test_label_value_is_bar():
    df = pd.DataFrame({"state": ["TEXAS", "OHIO"], "hail": [10, 5]})
    assert choose_visual(df) == ("bar", ("state", "hail"))


def test_year_trend_is_bar():
    df = pd.DataFrame({"year": [2000, 2001, 2002], "floods": [4, 6, 5]})
    assert choose_visual(df) == ("bar", ("year", "floods"))


def test_coordinates_make_a_map():
    df = pd.DataFrame({"begin_lat": [31.0, 32.0], "begin_lon": [-97.0, -96.0]})
    assert choose_visual(df) == ("map", ("begin_lat", "begin_lon"))


def test_zero_zero_or_null_coordinates_ignored():
    df = pd.DataFrame({"begin_lat": [0.0, None], "begin_lon": [0.0, None]})
    assert choose_visual(df)[0] == "none"


def test_too_many_bars_is_none():
    df = pd.DataFrame({"state": [f"S{i}" for i in range(150)], "n": range(150)})
    assert choose_visual(df)[0] == "none"


def test_chart_png_bytes():
    df = pd.DataFrame({"state": ["TEXAS", "OHIO"], "hail": [10, 5]})
    png = render_chart_png(df)
    assert png is None or png.startswith(b"\x89PNG")
