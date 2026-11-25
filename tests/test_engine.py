"""End-to-end question -> answer pipeline with a mocked Gemini client."""
import pandas as pd

TEXAS_2011 = ("SELECT COUNT(*) AS n FROM storm_events "
              "WHERE event_type='Tornado' AND state='TEXAS' AND year=2011")


def test_happy_path(make_engine):
    eng, client = make_engine([TEXAS_2011, "There were 2 tornadoes."])
    out = eng.ask("How many tornadoes in Texas in 2011?")
    assert out.error is None
    assert int(out.dataframe.iloc[0, 0]) == 2          # computed by the database
    assert out.answer == "There were 2 tornadoes."
    assert len(client.prompts) == 2                    # SQL call + summary call


def test_fenced_sql_is_cleaned(make_engine):
    eng, _ = make_engine([f"```sql\n{TEXAS_2011};\n```", "ok"])
    assert eng.ask("q").error is None


def test_self_correction_succeeds(make_engine):
    bad = "SELECT COUNT(*) FROM storm_event"           # wrong table name
    eng, client = make_engine([bad, TEXAS_2011, "fixed"])
    out = eng.ask("q")
    assert out.error is None and int(out.dataframe.iloc[0, 0]) == 2
    # the DuckDB error and the failed SQL were fed back to the model
    assert "previous query failed" in client.prompts[1]
    assert "storm_event" in client.prompts[1]


def test_gives_up_after_retries(make_engine):
    bad = "SELECT * FROM nope"
    eng, client = make_engine([bad, bad, bad], max_retries=2)
    out = eng.ask("q")
    assert out.error and "after retries" in out.error
    assert out.dataframe is None
    assert len(client.prompts) == 3                    # 1 try + 2 retries


def test_unsafe_sql_is_blocked_not_executed(make_engine):
    eng, _ = make_engine(["DROP TABLE storm_events"])
    out = eng.ask("delete everything")
    assert out.error and out.dataframe is None
    n = eng.con.execute("SELECT COUNT(*) FROM storm_events").fetchone()[0]
    assert n == 4                                      # table untouched


def test_rate_limit_stops_without_retry(make_engine):
    eng, client = make_engine([Exception("429 RESOURCE_EXHAUSTED")])
    out = eng.ask("q")
    assert "rate limit" in out.error.lower()
    assert len(client.prompts) == 1


def test_cannot_answer_sentinel(make_engine):
    eng, _ = make_engine(["SELECT 'CANNOT ANSWER'"])
    out = eng.ask("what is the meaning of life?")
    assert out.dataframe is None and "can't answer" in out.answer.lower()


def test_summary_failure_keeps_result(make_engine):
    eng, _ = make_engine([TEXAS_2011, Exception("boom")])
    out = eng.ask("q")
    assert out.dataframe is not None and "1 result row" in out.answer


def test_row_cap_applied(make_engine):
    eng, _ = make_engine(["SELECT * FROM storm_events", "ok"], max_rows=2)
    out = eng.ask("q")
    assert len(out.dataframe) == 2


def test_empty_result_is_handled(make_engine):
    sql = "SELECT * FROM storm_events WHERE year = 1800"
    eng, _ = make_engine([sql, "None found."])
    out = eng.ask("q")
    assert out.error is None and len(out.dataframe) == 0


def test_sum_over_no_rows_is_null(make_engine):
    sql = "SELECT SUM(damage_property) AS d FROM storm_events WHERE year = 1800"
    eng, _ = make_engine([sql, "No damage recorded."])
    out = eng.ask("q")
    assert out.error is None and pd.isna(out.dataframe.iloc[0, 0])
