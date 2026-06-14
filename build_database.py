"""
build_database.py
Milestone 1 of the NOAA Storm Events chatbot: the data foundation.

What it does, in order:
  1. Downloads every yearly "details" CSV from NOAA (cached locally so re-runs
     don't re-download).
  2. Cleans each file:
       - parses DAMAGE_PROPERTY / DAMAGE_CROPS  ("2.10B" -> 2_100_000_000.0)
       - builds real BEGIN/END timestamps from the numeric date parts
       - keeps a curated, clearly-typed subset of columns
  3. Loads everything into a single DuckDB file (storm_events.duckdb), inserting
     one year at a time so memory stays bounded.
  4. Writes schema_context.md -- the description of the table, its real value
     lists, and example queries -- which is the fuel that makes the
     text-to-SQL step accurate.

Run it:
    pip install pandas duckdb
    python build_database.py

The accuracy-critical functions (parse_damage, build_datetime, clean_dataframe)
are importable and unit-tested separately.
"""

from __future__ import annotations

import gzip
import io
import os
import re
import urllib.request
from datetime import datetime

import duckdb
import pandas as pd

NOAA_DIR = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
RAW_DIR = "noaa_raw"            # local cache for downloaded .csv.gz files
DB_PATH = "storm_events.duckdb"
TABLE = "storm_events"
CONTEXT_PATH = "schema_context.md"

# The curated columns we keep, mapped from NOAA's UPPERCASE names to clean
# lowercase snake_case, with the target type. "num" = numeric, "str" = text,
# "int" = integer. Timestamps are added separately.
COLUMN_MAP = {
    "EVENT_ID": ("event_id", "int"),
    "EPISODE_ID": ("episode_id", "int"),
    "STATE": ("state", "str"),
    "CZ_NAME": ("cz_name", "str"),
    "CZ_TYPE": ("cz_type", "str"),
    "WFO": ("wfo", "str"),
    "YEAR": ("year", "int"),
    "MONTH_NAME": ("month_name", "str"),
    "CZ_TIMEZONE": ("cz_timezone", "str"),
    "EVENT_TYPE": ("event_type", "str"),
    "INJURIES_DIRECT": ("injuries_direct", "int"),
    "INJURIES_INDIRECT": ("injuries_indirect", "int"),
    "DEATHS_DIRECT": ("deaths_direct", "int"),
    "DEATHS_INDIRECT": ("deaths_indirect", "int"),
    "MAGNITUDE": ("magnitude", "num"),
    "MAGNITUDE_TYPE": ("magnitude_type", "str"),
    "TOR_F_SCALE": ("tor_f_scale", "str"),
    "TOR_LENGTH": ("tor_length", "num"),
    "TOR_WIDTH": ("tor_width", "num"),
    "FLOOD_CAUSE": ("flood_cause", "str"),
    "CATEGORY": ("category", "str"),
    "SOURCE": ("source", "str"),
    "BEGIN_LAT": ("begin_lat", "num"),
    "BEGIN_LON": ("begin_lon", "num"),
    "END_LAT": ("end_lat", "num"),
    "END_LON": ("end_lon", "num"),
    "BEGIN_LOCATION": ("begin_location", "str"),
    "END_LOCATION": ("end_location", "str"),
    "EPISODE_NARRATIVE": ("episode_narrative", "str"),
    "EVENT_NARRATIVE": ("event_narrative", "str"),
    "DATA_SOURCE": ("data_source", "str"),
}

# Magnitude suffixes used in the damage columns.
_DAMAGE_MULT = {"H": 1e2, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


# ----------------------------------------------------------------------
# Accuracy-critical transforms (unit-tested)
# ----------------------------------------------------------------------
def parse_damage(value) -> float | None:
    """
    Convert a NOAA damage string into dollars.

        "2.10B" -> 2_100_000_000.0
        "10.00K" -> 10_000.0
        "0.00K"  -> 0.0
        "75"     -> 75.0
        ""/NaN   -> None   (blank means "not reported", not "zero")
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().upper()
    if s == "" or s in {"NAN", "NONE"}:
        return None
    mult = 1.0
    if s[-1] in _DAMAGE_MULT:
        mult = _DAMAGE_MULT[s[-1]]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def build_datetime(yearmonth, day, time) -> datetime | None:
    """
    Build a timestamp from NOAA's numeric parts, avoiding locale-dependent
    month-abbreviation parsing entirely.

        yearmonth=201104, day=27, time=1453 -> 2011-04-27 14:53:00
    """
    try:
        ym = int(yearmonth)
        d = int(day)
        t = int(time)
    except (ValueError, TypeError):
        return None
    year, month = ym // 100, ym % 100
    hh, mm = t // 100, t % 100
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        hh, mm = min(hh, 23), min(mm, 59)
    try:
        return datetime(year, month, d, hh, mm)
    except ValueError:
        return None


def clean_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    """Take one raw NOAA details DataFrame and return the cleaned, typed subset."""
    # Make sure every expected source column exists (older files are consistent,
    # but this guards against surprises).
    for src in list(COLUMN_MAP) + ["DAMAGE_PROPERTY", "DAMAGE_CROPS",
                                    "BEGIN_YEARMONTH", "BEGIN_DAY", "BEGIN_TIME",
                                    "END_YEARMONTH", "END_DAY", "END_TIME"]:
        if src not in raw.columns:
            raw[src] = pd.NA

    out = pd.DataFrame()
    for src, (dst, kind) in COLUMN_MAP.items():
        col = raw[src]
        if kind == "num":
            out[dst] = pd.to_numeric(col, errors="coerce")
        elif kind == "int":
            out[dst] = pd.to_numeric(col, errors="coerce").astype("Int64")
        else:
            out[dst] = col.astype("string").str.strip()

    out["damage_property"] = raw["DAMAGE_PROPERTY"].map(parse_damage)
    out["damage_crops"] = raw["DAMAGE_CROPS"].map(parse_damage)

    out["begin_datetime"] = [
        build_datetime(a, b, c)
        for a, b, c in zip(raw["BEGIN_YEARMONTH"], raw["BEGIN_DAY"], raw["BEGIN_TIME"])
    ]
    out["end_datetime"] = [
        build_datetime(a, b, c)
        for a, b, c in zip(raw["END_YEARMONTH"], raw["END_DAY"], raw["END_TIME"])
    ]
    out["begin_datetime"] = pd.to_datetime(out["begin_datetime"])
    out["end_datetime"] = pd.to_datetime(out["end_datetime"])
    return out


# ----------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------
def list_detail_files() -> list[str]:
    """Scrape NOAA's directory for the current 'details' filenames."""
    with urllib.request.urlopen(NOAA_DIR, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    # Filenames look like StormEvents_details-ftp_v1.0_d1950_c20210803.csv.gz
    names = re.findall(r"StormEvents_details-ftp_v1\.0_d\d{4}_c\d{8}\.csv\.gz", html)
    return sorted(set(names))


def download_if_needed(filename: str) -> str:
    """Download one file into the local cache (skip if already present)."""
    os.makedirs(RAW_DIR, exist_ok=True)
    path = os.path.join(RAW_DIR, filename)
    if os.path.exists(path):
        return path
    print(f"  downloading {filename}")
    urllib.request.urlretrieve(NOAA_DIR + filename, path)
    return path


# ----------------------------------------------------------------------
# Schema context (the accuracy fuel for text-to-SQL)
# ----------------------------------------------------------------------
COLUMN_DOCS = {
    "event_id": "Unique id for a single storm event.",
    "episode_id": "Id grouping related events from the same weather episode.",
    "state": "US state/territory name in UPPERCASE, e.g. 'TEXAS', 'FLORIDA'.",
    "cz_name": "County or forecast-zone name where the event occurred.",
    "cz_type": "'C' = county/parish, 'Z' = NWS forecast zone, 'M' = marine.",
    "wfo": "NWS Weather Forecast Office that issued the report.",
    "year": "Year of the event (integer).",
    "month_name": "Full month name, e.g. 'April'.",
    "begin_datetime": "Timestamp when the event began.",
    "end_datetime": "Timestamp when the event ended.",
    "cz_timezone": "Timezone of the begin/end times, e.g. 'CST-6'.",
    "event_type": "The kind of weather event. USE EXACT STRINGS (see value list).",
    "injuries_direct": "Injuries directly caused by the event.",
    "injuries_indirect": "Injuries indirectly caused by the event.",
    "deaths_direct": "Deaths directly caused by the event.",
    "deaths_indirect": "Deaths indirectly caused by the event.",
    "damage_property": "Property damage in US dollars (numeric; NULL if not reported).",
    "damage_crops": "Crop damage in US dollars (numeric; NULL if not reported).",
    "magnitude": "Numeric magnitude: wind speed (knots) for wind, hail size (inches) for hail.",
    "magnitude_type": "How magnitude was measured, e.g. 'EG', 'MG', 'MS', 'ES'.",
    "tor_f_scale": "Tornado intensity, e.g. 'EF0'..'EF5' (or legacy 'F0'..'F5').",
    "tor_length": "Tornado path length in miles.",
    "tor_width": "Tornado path width in yards.",
    "flood_cause": "Cause of a flood event, when applicable.",
    "category": "Event category, when applicable.",
    "source": "Who reported the event, e.g. 'Trained Spotter', 'Public'.",
    "begin_lat": "Latitude where the event began.",
    "begin_lon": "Longitude where the event began.",
    "end_lat": "Latitude where the event ended.",
    "end_lon": "Longitude where the event ended.",
    "begin_location": "Nearest place name to where the event began.",
    "end_location": "Nearest place name to where the event ended.",
    "episode_narrative": "Free-text summary of the whole weather episode.",
    "event_narrative": "Free-text summary of this specific event.",
    "data_source": "NOAA data source code.",
}

EXAMPLE_QUERIES = [
    ("How many tornadoes occurred in Texas in 2011?",
     "SELECT COUNT(*) FROM storm_events\n"
     "WHERE event_type = 'Tornado' AND state = 'TEXAS' AND year = 2011;"),
    ("What was the total property damage from hurricanes in 2005?",
     "SELECT SUM(damage_property) FROM storm_events\n"
     "WHERE event_type = 'Hurricane (Typhoon)' AND year = 2005;"),
    ("Which 5 states had the most hail events since 2015?",
     "SELECT state, COUNT(*) AS hail_events FROM storm_events\n"
     "WHERE event_type = 'Hail' AND year >= 2015\n"
     "GROUP BY state ORDER BY hail_events DESC LIMIT 5;"),
    ("What were the 10 deadliest events in 2011 by total deaths?",
     "SELECT event_type, state, cz_name, begin_datetime,\n"
     "       (deaths_direct + deaths_indirect) AS total_deaths\n"
     "FROM storm_events WHERE year = 2011\n"
     "ORDER BY total_deaths DESC LIMIT 10;"),
    ("How many flood events were there each year since 2000?",
     "SELECT year, COUNT(*) AS floods FROM storm_events\n"
     "WHERE event_type IN ('Flood', 'Flash Flood') AND year >= 2000\n"
     "GROUP BY year ORDER BY year;"),
]


def generate_schema_context(con: duckdb.DuckDBPyConnection) -> str:
    """Build the markdown context string from the live database."""
    ddl = con.execute(
        f"SELECT column_name, data_type FROM information_schema.columns "
        f"WHERE table_name = '{TABLE}' ORDER BY ordinal_position"
    ).fetchall()
    n_rows = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    yr_min, yr_max = con.execute(
        f"SELECT MIN(year), MAX(year) FROM {TABLE}"
    ).fetchone()
    event_types = con.execute(
        f"SELECT event_type, COUNT(*) c FROM {TABLE} "
        f"GROUP BY event_type ORDER BY c DESC"
    ).fetchall()
    states = con.execute(
        f"SELECT DISTINCT state FROM {TABLE} WHERE state IS NOT NULL ORDER BY state"
    ).fetchall()

    lines = []
    lines.append("# Storm Events database — schema and rules\n")
    lines.append(f"Table `{TABLE}` has {n_rows:,} rows covering {yr_min}–{yr_max}.\n")

    lines.append("## Columns\n")
    for name, dtype in ddl:
        doc = COLUMN_DOCS.get(name, "")
        lines.append(f"- `{name}` ({dtype}) — {doc}")
    lines.append("")

    lines.append("## CRITICAL: data completeness by year\n")
    lines.append(
        "Event recording expanded over time, so the same event types are NOT "
        "available for all years:\n"
        "- 1950–1954: only Tornado events.\n"
        "- 1955–1995: only Tornado, Thunderstorm Wind, and Hail.\n"
        "- 1996–present: all ~50 event types.\n"
        "When a question implies a trend across these boundaries (e.g. floods "
        "since 1950), warn that pre-1996 absence means *missing data*, not zero "
        "occurrences.\n"
    )

    lines.append("## Exact event_type values (use these strings verbatim)\n")
    for et, c in event_types:
        lines.append(f"- '{et}'  ({c:,})")
    lines.append("")

    lines.append("## Notes for writing correct SQL\n")
    lines.append("- `state` values are UPPERCASE full names, e.g. 'TEXAS'.")
    lines.append("- Damage columns are already numeric dollars; do NOT parse text.")
    lines.append("- Deaths/injuries come in direct and indirect columns; add both "
                 "for totals unless asked otherwise.")
    lines.append("- Use `begin_datetime` for date/time filtering.")
    lines.append(f"- {len(states)} distinct state/territory values are present.")
    lines.append("")

    lines.append("## Example questions and correct SQL\n")
    for q, sql in EXAMPLE_QUERIES:
        lines.append(f"Q: {q}\n```sql\n{sql}\n```\n")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------
def main() -> None:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = duckdb.connect(DB_PATH)

    files = list_detail_files()
    print(f"Found {len(files)} yearly files.")

    first = True
    for filename in files:
        path = download_if_needed(filename)
        with gzip.open(path, "rt", encoding="latin-1") as fh:
            raw = pd.read_csv(fh, low_memory=False)
        cleaned = clean_dataframe(raw)
        con.register("tmp_df", cleaned)
        if first:
            con.execute(f"CREATE TABLE {TABLE} AS SELECT * FROM tmp_df")
            first = False
        else:
            con.execute(f"INSERT INTO {TABLE} SELECT * FROM tmp_df")
        con.unregister("tmp_df")
        print(f"  loaded {filename}  (+{len(cleaned):,} rows)")

    # Helpful indexes for the kinds of filters the chatbot will use.
    for col in ("event_type", "state", "year"):
        con.execute(f"CREATE INDEX idx_{col} ON {TABLE}({col})")

    context = generate_schema_context(con)
    with open(CONTEXT_PATH, "w", encoding="utf-8") as fh:
        fh.write(context)

    total = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    print(f"\nDone. {total:,} rows in {DB_PATH}. Context written to {CONTEXT_PATH}.")
    con.close()


if __name__ == "__main__":
    main()
