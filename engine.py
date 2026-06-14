"""
engine.py
Milestone 2: the text-to-SQL engine, powered by Google Gemini.

Flow for each question:
    1. Ask Gemini to turn the question into a DuckDB SELECT query, giving it the
       schema_context.md as grounding (exact event_type strings, state casing,
       example queries, the 1996 completeness rule).
    2. Validate the SQL is read-only (SELECT/WITH only, no DDL/DML).
    3. Run it against a READ-ONLY DuckDB connection.
    4. If DuckDB errors, feed the error back to Gemini to self-correct (retry).
    5. Ask Gemini to phrase a plain-English answer from the result rows.

Accuracy levers in here: schema+value grounding, temperature 0 for SQL,
and the self-correction loop. Safety levers: read-only connection, a
SELECT-only validator, and a hard row cap.

Setup:
    pip install google-genai duckdb pandas
    # free API key: https://aistudio.google.com/app/apikey
    export GEMINI_API_KEY=your_key_here      # Windows: set GEMINI_API_KEY=...

Every call to Gemini goes through the single method `_call_model`, which makes
the rest of the engine easy to unit-test without an API key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import duckdb
import pandas as pd

DB_PATH = "storm_events.duckdb"
CONTEXT_PATH = "schema_context.md"

# Default model. Free-tier daily limits vary A LOT by model:
#   gemini-2.5-flash       ~250 requests/day  (good quality — default)
#   gemini-2.5-flash-lite  ~1,000+ requests/day (more headroom, slightly lower quality)
#   gemini-3.5-flash       ~20 requests/day   (newest/best, but tiny free quota)
# Each question uses ~2 calls (SQL + summary). Change this one line to switch.
DEFAULT_MODEL = "gemini-2.5-flash-lite"

MAX_ROWS = 10_000      # hard cap on rows returned to the UI / exports
MAX_RETRIES = 2        # self-correction attempts after a SQL execution error

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|MERGE|ATTACH|"
    r"DETACH|COPY|INSTALL|LOAD|PRAGMA|SET|EXPORT|CALL|VACUUM|GRANT|REVOKE)\b",
    re.IGNORECASE,
)

_SQL_INSTRUCTIONS = """You are an expert data analyst who writes DuckDB SQL for a single table named storm_events.
Use ONLY the columns and the EXACT value strings given in the schema below.

Rules:
- Output ONE valid DuckDB SELECT query and nothing else. No prose, no markdown, no code fences.
- SELECT (or WITH ... SELECT) only. Never modify data.
- Use event_type strings exactly as listed, and UPPERCASE state names (e.g. 'TEXAS').
- damage_property and damage_crops are already numeric US dollars; do not parse text.
- For death or injury totals, add the direct and indirect columns unless asked otherwise.
- Respect the data-completeness rules: event types other than Tornado, Hail, and
  Thunderstorm Wind do not exist before 1996.
- Add a sensible LIMIT (e.g. 100) when a query could return many individual rows.
- If the user asks to see, show, map, plot, or list WHERE events occurred, include
  begin_lat and begin_lon (plus event_type and state) in the SELECT so the
  locations can be plotted on a map.
- If the question cannot be answered from this table, return exactly:
  SELECT 'cannot answer' AS note
"""

_SUMMARY_INSTRUCTIONS = """You are answering a user's question about US storm events (NOAA Storm Events Database).
You are given their question and the result of a SQL query as a table.
Write a concise, accurate, plain-English answer using the specific numbers in the result.
Do not invent any data that is not in the result. If the result is empty, say that no
matching events were found. If the question covers years before 1996, briefly note that
earlier years only recorded Tornado, Hail, and Thunderstorm Wind events.
"""


@dataclass
class Answer:
    """Everything the UI and exporters need for one question."""
    question: str
    sql: str | None = None
    dataframe: pd.DataFrame | None = None
    answer: str = ""
    error: str | None = None


def _strip_sql_fences(text: str) -> str:
    """Remove ```sql ... ``` fences and a trailing semicolon if the model adds them."""
    text = (text or "").strip()
    m = re.match(r"^```(?:sql)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1).strip()
    return text.rstrip(";").strip()


def _is_rate_limit(err: Exception) -> bool:
    """True if an exception looks like a Gemini quota / rate-limit (HTTP 429)."""
    s = str(err).lower()
    return any(t in s for t in ("resource_exhausted", "429", "quota", "rate limit"))


_RATE_LIMIT_MSG = (
    "Gemini's free-tier rate limit was reached. Wait a minute and try again, or "
    "switch DEFAULT_MODEL in engine.py to a higher-quota model such as "
    "'gemini-2.5-flash-lite'."
)


def _strip_comments_and_strings(sql: str) -> str:
    """Blank out comments and string literals so keyword checks don't false-positive
    on, e.g., a place name that contains the word 'CREATE'."""
    s = re.sub(r"--[^\n]*", " ", sql)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"'(?:[^']|'')*'", " ", s)
    return s


class StormEventsEngine:
    def __init__(
        self,
        db_path: str = DB_PATH,
        context_path: str = CONTEXT_PATH,
        model: str = DEFAULT_MODEL,
        max_rows: int = MAX_ROWS,
        max_retries: int = MAX_RETRIES,
        summarize: bool = True,
        client=None,
    ):
        # read_only=True is the strongest guardrail: the data physically cannot
        # be changed through this connection, whatever SQL is generated.
        self.con = duckdb.connect(db_path, read_only=True)
        with open(context_path, encoding="utf-8") as fh:
            self.context = fh.read()
        self.model = model
        self.max_rows = max_rows
        self.max_retries = max_retries
        self.summarize = summarize
        self._client = client  # lazily created; injectable for testing

    # ---- the only place that talks to Gemini ----
    def _call_model(self, prompt: str, temperature: float = 0.0) -> str:
        if self._client is None:
            from google import genai
            self._client = genai.Client()  # picks up GEMINI_API_KEY
        from google.genai import types
        resp = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=temperature),
        )
        return resp.text or ""

    # ---- SQL generation ----
    def _build_sql_prompt(self, question, prior_sql=None, prior_error=None) -> str:
        parts = [_SQL_INSTRUCTIONS, "\n--- SCHEMA ---\n", self.context, "\n--- END SCHEMA ---\n"]
        if prior_sql and prior_error:
            parts.append(
                f"\nYour previous query failed and must be corrected.\n"
                f"Previous SQL:\n{prior_sql}\n\nDuckDB error:\n{prior_error}\n"
            )
        parts.append(f"\nQuestion: {question}\nSQL:")
        return "".join(parts)

    def _generate_sql(self, question, prior_sql=None, prior_error=None) -> str:
        raw = self._call_model(self._build_sql_prompt(question, prior_sql, prior_error), 0.0)
        return _strip_sql_fences(raw)

    # ---- safety validation ----
    def _validate(self, sql: str) -> None:
        if not sql:
            raise ValueError("The model returned an empty query.")
        cleaned = _strip_comments_and_strings(sql)
        if ";" in cleaned.strip().rstrip(";"):
            raise ValueError("Only a single SQL statement is allowed.")
        if not re.match(r"^\s*(select|with)\b", cleaned, re.IGNORECASE):
            raise ValueError("Only SELECT queries are allowed.")
        if _FORBIDDEN.search(cleaned):
            raise ValueError("Query contains a forbidden (non-SELECT) keyword.")

    # ---- execution (with hard row cap) ----
    def _execute(self, sql: str) -> pd.DataFrame:
        wrapped = f"SELECT * FROM (\n{sql}\n) AS _result LIMIT {self.max_rows}"
        return self.con.execute(wrapped).fetchdf()

    # ---- natural-language answer ----
    def _summarize(self, question: str, df: pd.DataFrame) -> str:
        preview = df.head(50).to_string(index=False)
        tail = "" if len(df) <= 50 else f"\n(showing first 50 of {len(df):,} rows)"
        prompt = (f"{_SUMMARY_INSTRUCTIONS}\nQuestion: {question}\n\n"
                  f"SQL result:\n{preview}{tail}\n\nAnswer:")
        return self._call_model(prompt, 0.2).strip()

    # ---- orchestration ----
    def ask(self, question: str) -> Answer:
        result = Answer(question=question)
        sql = None
        last_error = None

        for _ in range(self.max_retries + 1):
            try:
                sql = self._generate_sql(question, prior_sql=sql, prior_error=last_error)
                self._validate(sql)                 # raises ValueError -> terminal
                df = self._execute(sql)             # raises -> self-correct & retry
            except ValueError as e:
                result.sql = sql
                result.error = str(e)
                return result
            except Exception as e:
                if _is_rate_limit(e):                # quota hit: stop, don't retry
                    result.sql = sql
                    result.error = _RATE_LIMIT_MSG
                    return result
                last_error = str(e)                 # SQL ran but failed; try to fix it
                result.sql = sql
                continue

            result.sql = sql

            # "cannot answer" sentinel from the prompt rules
            if df.shape == (1, 1) and str(df.iloc[0, 0]).strip().lower() == "cannot answer":
                result.answer = ("I can't answer that from the storm events data. Try "
                                 "asking about event counts, damage, deaths, or locations.")
                return result

            result.dataframe = df

            # Summarize, but never let a failure here throw away a good result.
            if self.summarize:
                try:
                    result.answer = self._summarize(question, df)
                except Exception as e:
                    result.answer = f"Found {len(df):,} result row(s)."
                    if _is_rate_limit(e):
                        result.answer += (" (A written summary couldn't be generated — "
                                          "rate limit reached.)")
            else:
                result.answer = f"Returned {len(df):,} row(s)."
            return result

        result.error = f"Couldn't produce a working query after retries. Last error: {last_error}"
        return result


if __name__ == "__main__":
    # Quick manual smoke test (needs GEMINI_API_KEY and a built storm_events.duckdb)
    from dotenv import load_dotenv
    load_dotenv()
    engine = StormEventsEngine()
    out = engine.ask("How many tornadoes occurred in Texas in 2011?")
    print("SQL:", out.sql)
    print("Answer:", out.answer or out.error)
