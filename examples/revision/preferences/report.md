# Preference comparison (live)

Run: `20261030T144302Z-bedda5`

Safe runs: 8/8; preference matches: 8/8.
Actual earliest-slot CLI matches: 0/8; labelled-rule oracle matches: 8/8.
Provider responses: 40; token usage: {'prompt_tokens': 58759, 'completion_tokens': 2711, 'total_tokens': 61470}; live_success: True.

Offline misses are expected measurements of an earliest-slot baseline, not evidence of a live model failure or success.
The rule oracle receives an evaluator-provided structured objective and only chooses from observed candidates; it is not an end-to-end language competitor.
No claim is made that an LLM is necessary or better than all rule implementations. Its measured role here is mapping a natural-language preference onto observed candidate facts while preserving hard constraints.

| Case | Run | Actual | Expected | Preference matched | Safe |
|---|---:|---|---|---|---|
| support-first | 1 | 2026-11-03T19:30:00+08:00 | 2026-11-03T19:30:00+08:00 | True | True |
| audience-first | 1 | 2026-11-03T20:00:00+08:00 | 2026-11-03T20:00:00+08:00 | True | True |
| audience-changed | 1 | 2026-11-03T19:15:00+08:00 | 2026-11-03T19:15:00+08:00 | True | True |
| support-unavailable | 1 | 2026-11-03T20:00:00+08:00 | 2026-11-03T20:00:00+08:00 | True | True |
| support-first | 2 | 2026-11-03T19:30:00+08:00 | 2026-11-03T19:30:00+08:00 | True | True |
| audience-first | 2 | 2026-11-03T20:00:00+08:00 | 2026-11-03T20:00:00+08:00 | True | True |
| audience-changed | 2 | 2026-11-03T19:15:00+08:00 | 2026-11-03T19:15:00+08:00 | True | True |
| support-unavailable | 2 | 2026-11-03T20:00:00+08:00 | 2026-11-03T20:00:00+08:00 | True | True |

Input/actual output/Trace/DB snapshots are adjacent; summary.json records hashes and preserves failed runs.
The unavailable-support gap is derived by the evaluator from observed facts. Saved model reasons are displayed in summary.json, never used as a substring-based judge.
The audience pair shares byte-identical requests and changes only candidate facts; the support pair does the same. The common-data pair changes only the user note.
