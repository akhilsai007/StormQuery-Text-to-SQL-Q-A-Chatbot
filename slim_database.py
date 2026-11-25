"""
slim_database.py
One-time deployment prep: shrink storm_events.duckdb by dropping the large
free-text narrative columns (episode_narrative, event_narrative). These are not
used by the quantitative Q&A, and removing them cuts the file size dramatically
while keeping all rows and every queried column. Also regenerates the schema
context so it matches the slimmed columns.

Run once:
    python slim_database.py

Then follow the printed instructions to swap the slimmed file into place.
"""

import os

import duckdb

from build_database import CONTEXT_PATH, TABLE, generate_schema_context

SRC = "storm_events.duckdb"
DST = "storm_events_slim.duckdb"
DROP_COLUMNS = {"episode_narrative", "event_narrative"}


def slim(src: str, dst: str, context_path: str) -> tuple[int, int, list[str]]:
    """Copy `src` into `dst` minus DROP_COLUMNS; regenerate the schema context.
    Returns (row_count, columns_kept, kept_column_names)."""
    if os.path.exists(dst):
        os.remove(dst)

    # Figure out which columns to keep (everything except the narratives).
    con = duckdb.connect(src, read_only=True)
    kept = [r[0] for r in con.execute(
        f"SELECT column_name FROM information_schema.columns "
        f"WHERE table_name = '{TABLE}' ORDER BY ordinal_position"
    ).fetchall() if r[0] not in DROP_COLUMNS]
    con.close()
    col_list = ", ".join(f'"{c}"' for c in kept)

    # Build the slim database by copying only the kept columns.
    out = duckdb.connect(dst)
    out.execute(f"ATTACH '{src}' AS source (READ_ONLY)")
    out.execute(f"CREATE TABLE {TABLE} AS SELECT {col_list} FROM source.{TABLE}")
    out.execute("DETACH source")
    for c in ("event_type", "state", "year"):
        out.execute(f"CREATE INDEX idx_{c} ON {TABLE}({c})")

    # Regenerate the schema context so the model never references dropped columns.
    with open(context_path, "w", encoding="utf-8") as fh:
        fh.write(generate_schema_context(out))

    rows = out.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    out.close()
    return rows, len(kept), kept


def main() -> None:
    if not os.path.exists(SRC):
        raise SystemExit(f"{SRC} not found — run build_database.py first.")

    rows, n_cols, _ = slim(SRC, DST, CONTEXT_PATH)

    old_mb = os.path.getsize(SRC) / (1 << 20)
    new_mb = os.path.getsize(DST) / (1 << 20)
    print(f"Dropped columns: {sorted(DROP_COLUMNS)}")
    print(f"Rows: {rows:,} (unchanged)   Columns kept: {n_cols}")
    print(f"Size: {old_mb:.0f} MB  ->  {new_mb:.0f} MB")
    print(f"Schema context regenerated: {CONTEXT_PATH}")
    print("\nNext step — swap the slimmed file into place:")
    print(f"    rm {SRC} && mv {DST} {SRC}")


if __name__ == "__main__":
    main()
