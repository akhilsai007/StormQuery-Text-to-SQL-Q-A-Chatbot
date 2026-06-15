"""
app.py
Milestone 3: the chat interface.

Ties the pieces together:
  - engine.py      -> turns a question into SQL, runs it, writes an answer
  - exporters.py   -> CSV / Excel / PDF download buttons
  - this file      -> a Streamlit chat UI on top

Run it (with your venv active, GEMINI_API_KEY set, and storm_events.duckdb built):
    streamlit run app.py
"""

import os

import streamlit as st
from dotenv import load_dotenv

from engine import StormEventsEngine
from exporters import to_csv_bytes, to_excel_bytes, to_pdf_bytes
from visuals import choose_visual, map_points, render_chart_png

load_dotenv()  # read GEMINI_API_KEY (and any other vars) from a local .env file

DB_PATH = "storm_events.duckdb"

# For DEPLOYMENT only: the database is too large for the GitHub repo, so it's
# hosted as a GitHub Release asset and downloaded on first launch. Paste that
# asset's download URL here. Leave blank for local use (you already have the file).
DB_URL = "https://github.com/akhilsai007/StormQuery-Text-to-SQL-Q-A-Chatbot/releases/download/v1.0/storm_events.duckdb"  # e.g. "https://github.com/<user>/<repo>/releases/download/v1.0/storm_events.duckdb"

EXAMPLE_QUESTIONS = [
    "How many tornadoes hit Texas in 2011?",
    "Which 5 states had the most hail events since 2015?",
    "What was the total property damage from floods in 2017?",
]

st.set_page_config(page_title="Storm Events Chatbot", page_icon="⛈️", layout="centered")


def ensure_database() -> None:
    """If the database file is missing but DB_URL is set, download it (once),
    showing a progress bar. Does nothing when the file already exists locally."""
    if os.path.exists(DB_PATH) or not DB_URL:
        return
    import urllib.request

    bar = st.progress(0.0, text="Downloading the storm events database (first run only)…")

    def _hook(block_num, block_size, total_size):
        if total_size > 0:
            done = block_num * block_size
            pct = min(done / total_size, 1.0)
            bar.progress(pct, text=f"Downloading database… {done // (1 << 20)}/"
                                   f"{total_size // (1 << 20)} MB")

    try:
        urllib.request.urlretrieve(DB_URL, DB_PATH, reporthook=_hook)
    finally:
        bar.empty()


# ----------------------------------------------------------------------
# Startup checks: fail with a friendly message instead of a stack trace
# ----------------------------------------------------------------------
if not os.environ.get("GEMINI_API_KEY"):
    st.error(
        "**GEMINI_API_KEY is not set.** Create a file named `.env` in this folder "
        "containing your key, then restart the app:\n\n"
        "```\nGEMINI_API_KEY=your_key_here\n```\n\n"
        "Get a free key at https://aistudio.google.com/app/apikey"
    )
    st.stop()

ensure_database()  # download the DB on a fresh deployment if DB_URL is set

if not os.path.exists(DB_PATH):
    st.error(
        f"**{DB_PATH} not found.** Build it locally with `python build_database.py`, "
        "or (for deployment) set `DB_URL` near the top of app.py to the database's "
        "download URL."
    )
    st.stop()


# The engine opens a DuckDB connection and loads the schema context. Cache it so
# that work happens once, not on every interaction/rerun.
@st.cache_resource(show_spinner=False)
def get_engine() -> StormEventsEngine:
    return StormEventsEngine()


try:
    engine = get_engine()
except Exception as e:
    st.error(f"Couldn't start the engine: {e}")
    st.stop()


# ----------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "counter" not in st.session_state:
    st.session_state.counter = 0


def new_id() -> int:
    st.session_state.counter += 1
    return st.session_state.counter


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------
def render_visual(df) -> None:
    """Show a map or bar chart when the result shape suggests one.

    Wrapped in try/except: a visualization is a bonus, so a charting hiccup
    should never break the answer or the table.
    """
    try:
        kind, cols = choose_visual(df)
        if kind == "map":
            lat_col, lon_col = cols
            st.map(map_points(df, lat_col, lon_col), latitude=lat_col, longitude=lon_col)
        elif kind == "bar":
            x_col, y_col = cols
            st.bar_chart(df, x=x_col, y=y_col)
    except Exception:
        pass


def render_assistant(result, mid: int) -> None:
    """Render one assistant turn: answer, visual, table, SQL (collapsed), downloads."""
    if result.error:
        st.error(f"I couldn't answer that. Details: {result.error}")
        if result.sql:
            with st.expander("View attempted SQL"):
                st.code(result.sql, language="sql")
        return

    st.markdown(result.answer)

    df = result.dataframe
    if df is not None and len(df) > 0:
        render_visual(df)
        st.dataframe(df, use_container_width=True, hide_index=True)

        with st.expander("View the SQL used"):
            st.code(result.sql, language="sql")

        chart_png = render_chart_png(df)  # embedded in the PDF (None if no chart)
        c1, c2, c3 = st.columns(3)
        c1.download_button(
            "⬇ CSV", to_csv_bytes(df),
            file_name="storm_results.csv", mime="text/csv", key=f"csv_{mid}")
        c2.download_button(
            "⬇ Excel", to_excel_bytes(df, result.question),
            file_name="storm_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"xlsx_{mid}")
        c3.download_button(
            "⬇ PDF", to_pdf_bytes(df, result.question, result.answer, chart_png=chart_png),
            file_name="storm_results.pdf", mime="application/pdf", key=f"pdf_{mid}")
    elif result.sql:
        with st.expander("View the SQL used"):
            st.code(result.sql, language="sql")


# ----------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------
with st.sidebar:
    st.header("About")
    st.markdown(
        "Ask questions in plain English about the **NOAA Storm Events Database** "
        "(1950–present). Your question is turned into SQL, run against a local "
        "database of ~1.7 million events, and answered — with the data available "
        "to download."
    )
    st.caption(
        "Heads up: event recording expanded over time. Only tornado, hail, and "
        "thunderstorm-wind events exist before 1996, so trends reaching further "
        "back can be misleading."
    )
    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.rerun()


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
st.title("⛈️ Storm Events Chatbot")

# Render the conversation so far.
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["content"])
        else:
            render_assistant(msg["result"], msg["id"])

# Show clickable example questions only on a fresh conversation.
clicked = None
if not st.session_state.messages:
    st.markdown("##### Try one of these:")
    for col, example in zip(st.columns(len(EXAMPLE_QUESTIONS)), EXAMPLE_QUESTIONS):
        if col.button(example, use_container_width=True):
            clicked = example

# A typed question takes priority; otherwise use a clicked example.
prompt = st.chat_input("Ask about US storm events (1950–present)…") or clicked

if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt, "id": new_id()})

    with st.chat_message("assistant"):
        with st.spinner("Analyzing the data…"):
            result = engine.ask(prompt)
        mid = new_id()
        render_assistant(result, mid)
    st.session_state.messages.append({"role": "assistant", "result": result, "id": mid})
