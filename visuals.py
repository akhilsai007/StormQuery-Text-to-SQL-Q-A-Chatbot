"""
visuals.py
Decide which visualization (if any) suits a query result.

This is pure pandas logic with NO Streamlit import, so it can be unit-tested on
its own. app.py imports choose_visual() and map_points() and does the actual
rendering with st.map / st.bar_chart.

Rules:
  - MAP  if the result has usable latitude/longitude columns.
  - BAR  if the result is a clean label->value pair (e.g. state -> count) or a
         year trend (year -> count), with a sensible number of bars.
  - NONE otherwise (the table alone is clearer).
"""

from __future__ import annotations

import io

import pandas as pd
from pandas.api.types import is_numeric_dtype

_LAT_NAMES = ("begin_lat", "latitude", "lat")
_LON_NAMES = ("begin_lon", "longitude", "lon", "lng")


def _find(lower_map: dict, names) -> str | None:
    for n in names:
        if n in lower_map:
            return lower_map[n]
    return None


def map_points(df: pd.DataFrame, lat_col: str, lon_col: str) -> pd.DataFrame:
    """Numeric, non-null, non-(0,0) coordinate rows, ready to hand to st.map."""
    pts = df[[lat_col, lon_col]].apply(pd.to_numeric, errors="coerce").dropna()
    return pts[(pts[lat_col] != 0) | (pts[lon_col] != 0)]


def _looks_like_years(series: pd.Series) -> bool:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if len(vals) == 0:
        return False
    return bool(vals.between(1950, 2100).all()) and bool((vals == vals.round()).all())


def choose_visual(df: pd.DataFrame):
    """Return ('map', (lat, lon)) | ('bar', (x, y)) | ('none', None)."""
    if df is None or len(df) == 0:
        return ("none", None)

    cols = list(df.columns)
    lower = {str(c).lower(): c for c in cols}

    # 1) MAP -- whenever we have usable coordinates (even a single point).
    lat_col = _find(lower, _LAT_NAMES)
    lon_col = _find(lower, _LON_NAMES)
    if lat_col and lon_col and len(map_points(df, lat_col, lon_col)) >= 1:
        return ("map", (lat_col, lon_col))

    # 2) BAR -- a clean label->value pair, with a readable number of bars.
    if 2 <= len(df) <= 100:
        numeric = [c for c in cols if is_numeric_dtype(df[c])]
        non_numeric = [c for c in cols if c not in numeric]

        # one text label + one number  (e.g. state -> count, month -> count)
        if len(non_numeric) == 1 and len(numeric) == 1:
            return ("bar", (non_numeric[0], numeric[0]))

        # a year trend: exactly two numeric columns, one of which is a year axis
        if len(cols) == 2 and len(numeric) == 2:
            for cand in cols:
                if _looks_like_years(df[cand]):
                    other = next(c for c in cols if c != cand)
                    return ("bar", (cand, other))

    return ("none", None)


def _fmt(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{int(f):,}" if f.is_integer() else f"{f:,.1f}"


def render_chart_png(df: pd.DataFrame) -> bytes | None:
    """Render the result's visualization as a PNG (for embedding in the PDF).

    Returns bar-chart bytes for label->value results, a location scatter for
    coordinate results, or None when there's nothing to chart. Uses matplotlib's
    Figure directly (no pyplot) so it's safe to call from a server context.
    Wrapped in try/except: a charting failure must never break the download.
    """
    try:
        kind, cols = choose_visual(df)
        if kind == "none":
            return None

        from matplotlib.figure import Figure  # imported lazily

        fig = Figure(figsize=(6.5, 3.6), dpi=150)
        ax = fig.subplots()

        if kind == "bar":
            x_col, y_col = cols
            labels = df[x_col].astype(str).tolist()
            values = pd.to_numeric(df[y_col], errors="coerce").fillna(0).tolist()
            bars = ax.bar(range(len(labels)), values, color="#1F4E78")
            ax.set_xticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
            ax.set_ylabel(str(y_col), fontsize=9)
            ax.set_title(f"{y_col} by {x_col}", fontsize=11)
            if len(labels) <= 15:
                for rect, v in zip(bars, values):
                    ax.annotate(_fmt(v), (rect.get_x() + rect.get_width() / 2, v),
                                ha="center", va="bottom", fontsize=7)
            ax.margins(y=0.15)

        elif kind == "map":
            lat_col, lon_col = cols
            pts = map_points(df, lat_col, lon_col)
            ax.scatter(pts[lon_col], pts[lat_col], s=16, alpha=0.6,
                       color="#C0392B", edgecolors="none")
            ax.set_xlabel("Longitude", fontsize=9)
            ax.set_ylabel("Latitude", fontsize=9)
            ax.set_title(f"Event locations ({len(pts)} points)", fontsize=11)
            ax.set_aspect("equal", adjustable="datalim")
            ax.margins(0.1)
            ax.grid(True, linewidth=0.3, alpha=0.5)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        return buf.getvalue()
    except Exception:
        return None
