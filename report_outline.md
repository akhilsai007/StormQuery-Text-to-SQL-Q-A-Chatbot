# Capstone Report — Outline & Writing Guide

> **How to use this:** This is a *scaffold*, not the report. Each section lists
> what to cover and the key points from your project to draw on. Write the prose
> yourself, in your own words, and add your own experience and reflection — that's
> what makes it yours and what you'll be able to defend in a demo or viva.
>
> Suggested title (rename to taste): *"A Natural-Language Chatbot for Querying the
> NOAA Storm Events Database via Text-to-SQL."*

---

## 1. Abstract  (~150–200 words; write this LAST)
A short summary of: what you built (a chatbot that answers plain-English questions
about NOAA storm data), the core approach (translating questions into SQL), and
the headline outcome (accurate, verifiable answers with downloadable reports and
automatic visualizations).

## 2. Introduction & Problem Statement  (~1 page)
- The assignment: build a chatbot that answers questions from NOAA's National
  Weather Service Storm Events dataset, with answers downloadable in a file.
- The dataset in one line: NOAA Storm Events, ~1.7 million records, 1950–2026.
- **The key framing to state up front:** this is *structured, numerical* data, and
  almost every useful question is an *aggregation* (counts, sums, rankings). Name
  this early — it justifies your whole approach.
- Your objectives: accuracy, verifiability, downloadable output, a usable interface.
- *Prompt:* Who would use this and why is it useful?

## 3. The Dataset  (~1 page)
- Source and scale: NOAA NCEI; the bulk "details" files; ~1.7M rows across 76 years.
- Key fields: event_type, state, dates, deaths/injuries, property/crop damage,
  coordinates, and free-text narratives.
- **Data challenges you handled** (include these — they show rigor):
  - Damage stored as text with K/M/B suffixes (e.g. `"2.10B"`) → parsed to numeric
    dollars so it can be summed.
  - Dates split across numeric columns → reconstructed into real timestamps.
  - **The 1996 completeness boundary:** only tornado, hail, and thunderstorm-wind
    events exist before 1996; all ~50 types exist after. This is a standout point —
    it shows genuine understanding of the data, and you built it into the system.
- *Prompt:* a small table of a few example columns helps here.

## 4. System Architecture & Approach  (~1–2 pages) — **THE CORE SECTION**
- **The central decision: text-to-SQL, not retrieval (RAG).** Explain *why*:
  vector/semantic search retrieves rows that are *similar*, but it cannot return the
  *complete* set needed for a COUNT or SUM, and language models are unreliable at
  arithmetic over thousands of retrieved rows. For aggregation questions over
  structured data, the right design is to translate the question into SQL and let
  the database compute the exact answer. **This is your key intellectual point —
  make the contrast explicit; it shows you understood the problem space.**
- The data flow: question → model writes SQL → SQL is validated → executed on
  DuckDB → results → model writes a plain-English answer → downloadable.
- Include an architecture diagram (draw your own version of the flow).

## 5. Technology Choices & Justifications  (~1–2 pages)
State each choice *and the tradeoff reasoning*:
- **Database — DuckDB.** Columnar/analytical engine built for the aggregation
  queries you run; embedded (a single file, no server) like SQLite but far faster
  on wide-table aggregations. Chosen over Postgres (no need for a server when the
  workload is read-only and single-user) and over SQLite (slower on analytics).
- **Language model — Google Gemini 2.5 Flash.** Free-tier eligible and strong at
  SQL. **The deliberate tradeoff:** you chose 2.5 Flash over the newer, higher-
  benchmark 3.5 Flash because free-tier *capacity* (~250 requests/day vs ~20)
  mattered more for building and demoing than a marginal quality gain — and because
  your heavy schema grounding means a mid-tier model produces correct SQL reliably.
  *(This "capacity-to-quality tradeoff for a grounded task" is a sophisticated point
  — emphasize it.)*
- **Interface — Streamlit.** Pure-Python, built-in chat and download components,
  quick to build, free hosting available.
- **Supporting libraries:** pandas (data cleaning), reportlab (PDF), openpyxl
  (Excel), matplotlib (charts), python-dotenv (key management).

## 6. Implementation  (~2–3 pages)
Walk through each component (one short subsection each):
- **Data pipeline:** download → clean (damage parsing, timestamp building, typing)
  → load into DuckDB → auto-generate a schema-context file.
- **The engine — how a question becomes an answer:**
  - The model receives the schema context (exact event_type values, state casing,
    example queries, the 1996 rule) and writes SQL.
  - **Accuracy levers** (list them explicitly): schema + exact-value grounding;
    temperature 0 for determinism; few-shot examples; a *self-correction loop* that
    feeds a failed query's database error back to the model to fix.
  - **Safety guardrails** (two independent layers): a read-only database connection
    *and* a validator that rejects anything that isn't a single SELECT; plus a hard
    row cap.
- **The interface:** chat, the result table, on-screen visualizations, and a
  collapsible panel showing the generated SQL (transparency → verifiable answers).
- **Downloadable output:** CSV (raw data), Excel (styled + context), PDF (a report
  with the chart embedded).
- **Visualizations:** automatic selection — a map for results with coordinates, a
  bar chart for label→value and year-trend results, a table otherwise.

## 7. Challenges & Solutions  (~1–2 pages) — **shows problem-solving**
Frame each as *problem → diagnosis → solution → lesson*:
- **Empty-result PDF crash:** a SUM over zero matching rows returns NULL → NaN,
  which broke a length check during PDF generation (surfaced by the new pandas 3.0
  behavior). Fixed with defensive cell-to-text coercion. *Lesson:* test edge-case
  questions ("nothing found" is a different code path).
- **API rate limits:** the newest model had a tiny free quota; you diagnosed that
  quotas are per-model, switched to a higher-capacity model, and added graceful
  rate-limit handling. *Lesson:* match the model to real-world constraints.
- **Map in the PDF:** a true tiled map needs heavy geospatial libraries that are
  hard to install; you chose a coordinate scatter to keep the project portable.
  *Lesson:* a pragmatic, installable solution beats a fragile "perfect" one.
- *(Optional)* practical environment lessons (virtual environments, key management).

## 8. Results & Demonstration  (~1–2 pages)
- Show 4–5 example questions spanning types: a count, a damage sum, a ranking (with
  its bar chart), a map question, and one crossing the 1996 boundary (to show the
  caveat appearing).
- Include screenshots: the chat, a bar chart, a map, and a downloaded PDF.
- Note verifiability: because the SQL is shown, any answer can be checked.

## 9. Limitations & Future Work  (~1 page)
- Free-tier rate limits.
- The PDF map is a coordinate scatter, not a tiled map.
- Descriptive/narrative questions aren't the strength — a future *hybrid* could add
  retrieval (RAG) over the narrative fields for "describe what happened" questions,
  complementing the text-to-SQL backbone.
- Deployment to a public URL (if not completed).
- Possible extensions: conversation memory for follow-ups, more chart types, using
  the fatalities/locations files.

## 10. Conclusion  (~½ page)
What you accomplished and what you learned: architecture thinking, the
retrieval-vs-SQL insight, engineering tradeoffs, and the value of testing and
defensive coding.

## 11. References
NOAA NCEI Storm Events Database; Google Gemini API documentation; DuckDB; Streamlit;
and any other sources you used.

---

### Where to start
Don't write top-to-bottom. Easiest first wins: try **Section 7 (Challenges)** or
**Section 3 (Dataset)** to build momentum — they're concrete and you lived them.
Save the **Abstract** for last. Aim to get a rough draft of every section down
before polishing any single one.
