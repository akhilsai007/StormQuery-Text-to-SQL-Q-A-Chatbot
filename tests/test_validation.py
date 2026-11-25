"""SQL safety layer: only a single SELECT may ever reach the database."""
import pytest

from engine import _strip_sql_fences, _strip_comments_and_strings, _is_rate_limit


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM storm_events",
    "select * from storm_events where state = 'TEXAS'",
    "WITH t AS (SELECT * FROM storm_events) SELECT COUNT(*) FROM t",
    "SELECT * FROM storm_events WHERE cz_name = 'CREATE ME'",   # keyword inside a string
    "SELECT 1 -- DROP TABLE storm_events",                      # keyword inside a comment
])
def test_valid_queries_pass(make_engine, sql):
    eng, _ = make_engine([])
    eng._validate(sql)  # must not raise


@pytest.mark.parametrize("sql", [
    "DROP TABLE storm_events",
    "DELETE FROM storm_events",
    "UPDATE storm_events SET year = 0",
    "INSERT INTO storm_events VALUES (1)",
    "CREATE TABLE x AS SELECT 1",
    "ATTACH 'other.db' AS o",
    "COPY storm_events TO 'out.csv'",
    "PRAGMA database_list",
    "SELECT 1; DROP TABLE storm_events",       # stacked statement
    "SELECT 1; SELECT 2",                      # two statements
    "SELECT * FROM storm_events; DELETE FROM storm_events",
    "",                                        # empty
])
def test_unsafe_queries_rejected(make_engine, sql):
    eng, _ = make_engine([])
    with pytest.raises(ValueError):
        eng._validate(sql)


def test_trailing_semicolon_is_fine(make_engine):
    eng, _ = make_engine([])
    eng._validate("SELECT 1;")


def test_connection_is_read_only(make_engine):
    """Second guardrail: even if validation were bypassed, writes must fail."""
    eng, _ = make_engine([])
    with pytest.raises(Exception):
        eng.con.execute("DELETE FROM storm_events")


def test_strip_sql_fences():
    assert _strip_sql_fences("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert _strip_sql_fences("SELECT 1;") == "SELECT 1"
    assert _strip_sql_fences(None) == ""


def test_strip_comments_and_strings():
    out = _strip_comments_and_strings("SELECT 'DROP' -- DELETE\n/* CREATE */ 1")
    assert "DROP" not in out and "DELETE" not in out and "CREATE" not in out


def test_rate_limit_detection():
    assert _is_rate_limit(Exception("429 RESOURCE_EXHAUSTED"))
    assert _is_rate_limit(Exception("You exceeded your quota"))
    assert not _is_rate_limit(Exception("Catalog Error: table not found"))
