import os
import sys

import duckdb
import pytest

# Make the project root importable when running `pytest` from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import StormEventsEngine  # noqa: E402


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeClient:
    """Stands in for google.genai.Client. Replays scripted replies in order and
    records every prompt, so tests need no API key and cost nothing.
    An item that is an Exception instance is raised instead of returned."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.models = self

    def generate_content(self, model, contents, config=None):
        self.prompts.append(contents)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)


@pytest.fixture
def make_engine(tmp_path):
    """Build a tiny storm_events database and return a factory for engines."""
    db = str(tmp_path / "test.duckdb")
    con = duckdb.connect(db)
    con.execute(
        "CREATE TABLE storm_events (event_id BIGINT, state VARCHAR, year BIGINT, "
        "event_type VARCHAR, deaths_direct BIGINT, deaths_indirect BIGINT, "
        "damage_property DOUBLE, begin_lat DOUBLE, begin_lon DOUBLE)"
    )
    con.execute(
        "INSERT INTO storm_events VALUES "
        "(1,'TEXAS',2011,'Tornado',1,0,1000.0,31.0,-97.0),"
        "(2,'TEXAS',2011,'Tornado',0,0,NULL,32.0,-96.0),"
        "(3,'TEXAS',2012,'Hail',0,0,500.0,30.0,-95.0),"
        "(4,'OKLAHOMA',2011,'Tornado',2,1,2000.0,35.0,-97.5)"
    )
    con.close()
    ctx = tmp_path / "ctx.md"
    ctx.write_text("# test schema", encoding="utf-8")

    def _make(replies, **kw):
        client = FakeClient(replies)
        eng = StormEventsEngine(db_path=db, context_path=str(ctx), client=client, **kw)
        return eng, client

    return _make
