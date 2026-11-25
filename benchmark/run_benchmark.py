"""
StormQuery evaluation / benchmarking harness.

For every benchmark question it:
  1. asks the real engine (Gemini -> SQL -> DuckDB),
  2. runs the verified REFERENCE SQL on the same database,
  3. compares the two result sets,
and reports execution accuracy, valid-SQL rate, self-correction success,
guardrail accuracy, latency (avg / p50 / p95) and token usage.

Usage (from the project root, venv active, GEMINI_API_KEY in .env):
    python benchmark/run_benchmark.py --dry-run        # NO API calls: just run the reference SQL and print answers
    python benchmark/run_benchmark.py --limit 5        # quick smoke test
    python benchmark/run_benchmark.py                  # full run
    python benchmark/run_benchmark.py --model gemini-2.5-flash --sleep 6
Optional cost: --price-in 0.10 --price-out 0.40   (USD per 1M tokens; YOU supply current prices)
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import StormEventsEngine, DEFAULT_MODEL, _RATE_LIMIT_MSG  # noqa: E402
from questions import QUESTIONS, UNSAFE_SQL, ADVERSARIAL  # noqa: E402

ANSWERABLE = {"easy", "medium", "hard", "edge"}


# ----------------------------------------------------------------------
# Result comparison
# ----------------------------------------------------------------------
def _norm(v):
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        v = float(v)
        return None if math.isnan(v) else v
    if isinstance(v, (pd.Timestamp,)):
        return str(v)
    return v


def _cell_equal(a, b) -> bool:
    a, b = _norm(a), _norm(b)
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if float(a).is_integer() and float(b).is_integer():
            return float(a) == float(b)               # counts must match EXACTLY
        return math.isclose(a, b, rel_tol=1e-2, abs_tol=1e-9)   # non-integers: 1% tolerance
    return str(a).strip().lower() == str(b).strip().lower()


def _rows(df: pd.DataFrame):
    rows = [tuple(_norm(v) for v in r) for r in df.itertuples(index=False, name=None)]
    return sorted(rows, key=lambda r: tuple(str(round(x, 3)) if isinstance(x, float) else str(x) for x in r))


def _rows_equal(a, b) -> bool:
    return len(a) == len(b) and all(
        len(x) == len(y) and all(_cell_equal(i, j) for i, j in zip(x, y)) for x, y in zip(a, b))


def compare(gen: pd.DataFrame, ref: pd.DataFrame) -> str:
    """'exact' | 'extra_columns' | 'mismatch'.  Row order is ignored."""
    if gen.shape == ref.shape and _rows_equal(_rows(gen), _rows(ref)):
        return "exact"
    if len(gen) != len(ref) or gen.shape[1] < ref.shape[1]:
        return "mismatch"
    # lenient: the generated result may carry EXTRA columns, but every reference column must appear
    mapping, used = [], set()
    for rc in range(ref.shape[1]):
        ref_col = sorted(_rows(ref.iloc[:, [rc]]))
        for gc in range(gen.shape[1]):
            if gc in used:
                continue
            if _rows_equal(sorted(_rows(gen.iloc[:, [gc]])), ref_col):
                mapping.append(gc); used.add(gc); break
        else:
            return "mismatch"
    projected = gen.iloc[:, mapping]
    return "extra_columns" if _rows_equal(_rows(projected), _rows(ref)) else "mismatch"


# ----------------------------------------------------------------------
# Token metering
# ----------------------------------------------------------------------
class Meter:
    def __init__(self):
        self.inp = self.out = self.calls = 0

    def wrap(self, client):
        orig = client.models.generate_content

        def wrapped(*a, **k):
            r = orig(*a, **k)
            u = getattr(r, "usage_metadata", None)
            self.calls += 1
            self.inp += getattr(u, "prompt_token_count", 0) or 0
            self.out += getattr(u, "candidates_token_count", 0) or 0
            return r
        client.models.generate_content = wrapped
        return client


def pctl(xs, p):
    return float(np.percentile(xs, p)) if xs else float("nan")


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------
def run_one(eng, meter, q, sleep):
    attempts = {"n": 0}
    orig_gen = eng._generate_sql

    def counting_gen(*a, **k):
        attempts["n"] += 1
        return orig_gen(*a, **k)
    eng._generate_sql = counting_gen

    i0, o0 = meter.inp, meter.out
    t0 = time.perf_counter()
    waited = 0.0
    out = eng.ask(q["question"])
    if out.error == _RATE_LIMIT_MSG:                      # one patient retry
        eng._generate_sql = orig_gen
        time.sleep(65)
        waited = 65.0                                      # not counted as latency
        t0 = time.perf_counter()
        attempts["n"] = 0
        eng._generate_sql = counting_gen
        out = eng.ask(q["question"])
    latency = time.perf_counter() - t0
    eng._generate_sql = orig_gen

    rec = {"id": q["id"], "category": q["category"], "question": q["question"],
           "generated_sql": out.sql, "reference_sql": q["reference_sql"],
           "latency_s": round(latency, 3), "attempts": attempts["n"], "error": out.error,
           "tokens_in": meter.inp - i0, "tokens_out": meter.out - o0,
           "rate_limited": out.error == _RATE_LIMIT_MSG}

    if q["category"] == "unanswerable":
        declined = out.dataframe is None
        rec["status"] = "pass" if declined else "fail_answered"
        rec["detail"] = out.answer if declined else "answered an unanswerable question"
    else:
        rec["executed"] = out.dataframe is not None
        if out.dataframe is None:
            rec["status"] = "fail_no_result"
        else:
            ref = eng.con.execute(q["reference_sql"]).fetchdf()
            verdict = compare(out.dataframe, ref)
            rec["status"] = {"exact": "pass", "extra_columns": "pass_extra_columns",
                             "mismatch": "fail_wrong_result"}[verdict]
            if verdict == "mismatch":
                rec["detail"] = {"generated_head": out.dataframe.head(3).astype(str).values.tolist(),
                                 "reference_head": ref.head(3).astype(str).values.tolist(),
                                 "generated_rows": len(out.dataframe), "reference_rows": len(ref)}
    time.sleep(sleep)
    return rec


def guardrails(eng, meter, sleep):
    static = []
    for sql in UNSAFE_SQL:
        try:
            eng._validate(sql); blocked = False
        except ValueError:
            blocked = True
        static.append({"sql": sql, "blocked": blocked})

    adversarial = []
    rows_before = eng.con.execute("SELECT COUNT(*) FROM storm_events").fetchone()[0]
    for prompt in ADVERSARIAL:
        out = eng.ask(prompt)
        safe = True
        if out.dataframe is not None:                      # something executed -> it must be a SELECT
            try:
                eng._validate(out.sql)
            except ValueError:
                safe = False
        adversarial.append({"prompt": prompt, "sql": out.sql, "error": out.error, "safe": safe,
                            "inconclusive": out.error == _RATE_LIMIT_MSG})
        time.sleep(sleep)
    rows_after = eng.con.execute("SELECT COUNT(*) FROM storm_events").fetchone()[0]
    return static, adversarial, rows_before == rows_after


def summarize(records, static, adversarial, intact, meter, a):
    ans = [r for r in records if r["category"] in ANSWERABLE]
    un = [r for r in records if r["category"] == "unanswerable"]
    strict = sum(r["status"] == "pass" for r in ans)
    lenient = sum(r["status"] in ("pass", "pass_extra_columns") for r in ans)
    executed = sum(r.get("executed", False) for r in ans)
    retried = [r for r in ans if r["attempts"] > 1]
    lat = [r["latency_s"] for r in records]
    S = {
        "model": a.model, "questions_answerable": len(ans), "questions_unanswerable": len(un),
        "execution_accuracy_strict": strict / len(ans) if ans else None,
        "execution_accuracy_lenient": lenient / len(ans) if ans else None,
        "valid_sql_rate": executed / len(ans) if ans else None,
        "self_correction_attempted": len(retried),
        "self_correction_recovered": sum(r.get("executed", False) for r in retried),
        "self_correction_correct": sum(r["status"] in ("pass", "pass_extra_columns") for r in retried),
        "unanswerable_handled": sum(r["status"] == "pass" for r in un),
        "guardrail_static_blocked": sum(s["blocked"] for s in static), "guardrail_static_total": len(static),
        "guardrail_adversarial_inconclusive": sum(x.get("inconclusive", False) for x in adversarial),
        "guardrail_adversarial_safe": sum(x["safe"] and not x.get("inconclusive", False) for x in adversarial), "guardrail_adversarial_total": len(adversarial),
        "database_unchanged": intact,
        "latency_avg_s": float(np.mean(lat)) if lat else None, "latency_p50_s": pctl(lat, 50),
        "latency_p95_s": pctl(lat, 95),
        "tokens_in": meter.inp, "tokens_out": meter.out, "api_calls": meter.calls,
    }
    if a.price_in is not None and a.price_out is not None:
        S["estimated_cost_usd"] = meter.inp / 1e6 * a.price_in + meter.out / 1e6 * a.price_out
    by_cat = {}
    for c in ["easy", "medium", "hard", "edge", "unanswerable"]:
        rs = [r for r in records if r["category"] == c]
        if rs:
            by_cat[c] = (sum(r["status"] in ("pass", "pass_extra_columns") for r in rs), len(rs))
    return S, by_cat


def write_report(path, S, by_cat, records, static, adversarial):
    pct = lambda x: "n/a" if x is None else f"{x:.1%}"
    L = [f"# StormQuery benchmark report", f"", f"- Model: `{S['model']}`  |  Run: {datetime.now():%Y-%m-%d %H:%M}",
         f"- Questions: {S['questions_answerable']} answerable + {S['questions_unanswerable']} unanswerable", "",
         "## Headline metrics", "", "| Metric | Value |", "|---|---|",
         f"| Execution accuracy (strict: exact match) | {pct(S['execution_accuracy_strict'])} |",
         f"| Execution accuracy (lenient: extra columns allowed) | {pct(S['execution_accuracy_lenient'])} |",
         f"| Valid-SQL rate (executed successfully) | {pct(S['valid_sql_rate'])} |",
         f"| Self-correction (questions needing a retry) | {S['self_correction_attempted']} attempted, "
         f"{S['self_correction_recovered']} recovered, {S['self_correction_correct']} correct |",
         f"| Unanswerable questions declined | {S['unanswerable_handled']}/{S['questions_unanswerable']} |",
         f"| Guardrail: unsafe SQL blocked | {S['guardrail_static_blocked']}/{S['guardrail_static_total']} |",
         f"| Guardrail: adversarial prompts safe | {S['guardrail_adversarial_safe']}/{S['guardrail_adversarial_total']}"
         f"{' (' + str(S['guardrail_adversarial_inconclusive']) + ' inconclusive: rate-limited)' if S['guardrail_adversarial_inconclusive'] else ''} |",
         f"| Database unchanged after run | {S['database_unchanged']} |",
         f"| Latency avg / p50 / p95 (s) | {S['latency_avg_s']:.2f} / {S['latency_p50_s']:.2f} / {S['latency_p95_s']:.2f} |",
         f"| Tokens in / out ({S['api_calls']} API calls) | {S['tokens_in']:,} / {S['tokens_out']:,} |"]
    if "estimated_cost_usd" in S:
        L.append(f"| Estimated cost (USD, from supplied prices) | ${S['estimated_cost_usd']:.4f} |")
    L += ["", "## By category (passed / total)", "", "| Category | Passed |", "|---|---|"]
    L += [f"| {c} | {p}/{t} |" for c, (p, t) in by_cat.items()]
    fails = [r for r in records if r["status"].startswith("fail")]
    L += ["", f"## Failures ({len(fails)})", ""]
    for r in fails:
        L += [f"### {r['id']} ({r['category']}) — {r['status']}", f"**Q:** {r['question']}", "",
              f"Generated SQL:\n```sql\n{r['generated_sql']}\n```"]
        if r["reference_sql"]:
            L += [f"Reference SQL:\n```sql\n{r['reference_sql']}\n```"]
        if r.get("error"):
            L += [f"Error: `{r['error']}`"]
        if r.get("detail") and isinstance(r["detail"], dict):
            L += [f"Generated head: `{r['detail']['generated_head']}` ({r['detail']['generated_rows']} rows)  ",
                  f"Reference head: `{r['detail']['reference_head']}` ({r['detail']['reference_rows']} rows)"]
        L.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--db", default=os.path.join(ROOT, "storm_events.duckdb"))
    ap.add_argument("--context", default=os.path.join(ROOT, "schema_context.md"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", help="comma-separated question ids, e.g. E1,M3")
    ap.add_argument("--categories", help="comma-separated, e.g. easy,hard")
    ap.add_argument("--sleep", type=float, default=8.0, help="seconds between questions (rate limits)")
    ap.add_argument("--skip-guardrails", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="run reference SQL only; no API calls")
    ap.add_argument("--price-in", type=float); ap.add_argument("--price-out", type=float)
    ap.add_argument("--out", default=os.path.join(ROOT, "benchmark", "results"))
    a = ap.parse_args()

    qs = QUESTIONS
    if a.ids:
        want = {x.strip().upper() for x in a.ids.split(",")}; qs = [q for q in qs if q["id"] in want]
    if a.categories:
        want = {x.strip().lower() for x in a.categories.split(",")}; qs = [q for q in qs if q["category"] in want]
    if a.limit:
        qs = qs[:a.limit]

    if a.dry_run:
        import duckdb
        con = duckdb.connect(a.db, read_only=True)
        bad = 0
        for q in qs:
            if not q["reference_sql"]:
                print(f"{q['id']:>3}  (unanswerable - no reference)"); continue
            try:
                df = con.execute(q["reference_sql"]).fetchdf()
                print(f"{q['id']:>3}  {q['question']}\n     -> {len(df)} row(s); first: {df.head(1).values.tolist()}")
            except Exception as e:
                bad += 1; print(f"{q['id']:>3}  REFERENCE SQL FAILED: {e}")
        print(f"\nDry run complete. Reference SQL failures: {bad}")
        return

    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY not set (put it in .env).")
    from google import genai
    meter = Meter()
    client = meter.wrap(genai.Client())
    eng = StormEventsEngine(db_path=a.db, context_path=a.context, model=a.model, summarize=False, client=client)

    records = []
    for n, q in enumerate(qs, 1):
        r = run_one(eng, meter, q, a.sleep)
        records.append(r)
        print(f"[{n}/{len(qs)}] {r['id']:>3} {r['status']:<20} {r['latency_s']:>6.2f}s  attempts={r['attempts']}  {q['question'][:60]}")
        if r["rate_limited"]:
            print("Rate limit persisted - stopping early and saving partial results."); break

    static, adversarial, intact = ([], [], True) if a.skip_guardrails else guardrails(eng, meter, a.sleep)
    S, by_cat = summarize(records, static, adversarial, intact, meter, a)

    os.makedirs(a.out, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(a.out, f"{stamp}_{a.model}")
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump({"summary": S, "by_category": by_cat, "records": records,
                   "guardrails_static": static, "guardrails_adversarial": adversarial}, fh, indent=2, default=str)
    write_report(base + ".md", S, by_cat, records, static, adversarial)
    print("\n" + open(base + ".md", encoding="utf-8").read().split("## Failures")[0])
    print(f"Saved: {base}.md / .json")


if __name__ == "__main__":
    main()
