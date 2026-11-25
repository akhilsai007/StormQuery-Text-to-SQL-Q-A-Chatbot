# StormQuery benchmark report

- Model: `gemini-flash-lite-latest`  |  Run: 2026-11-25 14:31
- Questions: 32 answerable + 4 unanswerable

## Headline metrics

| Metric | Value |
|---|---|
| Execution accuracy (strict: exact match) | 93.8% |
| Execution accuracy (lenient: extra columns allowed) | 100.0% |
| Valid-SQL rate (executed successfully) | 100.0% |
| Self-correction (questions needing a retry) | 1 attempted, 1 recovered, 1 correct |
| Unanswerable questions declined | 4/4 |
| Guardrail: unsafe SQL blocked | 12/12 |
| Guardrail: adversarial prompts safe | 3/3 |
| Database unchanged after run | True |
| Latency avg / p50 / p95 (s) | 2.77 / 1.63 / 5.68 |
| Tokens in / out (39 API calls) | 97,058 / 1,336 |

## By category (passed / total)

| Category | Passed |
|---|---|
| easy | 8/8 |
| medium | 10/10 |
| hard | 8/8 |
| edge | 6/6 |
| unanswerable | 4/4 |

## Failures (0)
