# live evaluation

Run: `20261030T011613Z-d3322d`

Passed: 15/15 runs; 210/210 assertions.
Actual provider responses: 84.

Offline results verify process constraints only. Live results are a finite synthetic sample, not a general accuracy estimate.

| Case | Run | Expected | Actual | Passed | Steps |
|---|---:|---|---|---|---:|
| time-conflict | 1 | pending_confirmation | pending_confirmation | True | 5 |
| window-authorized | 1 | completed | completed | True | 5 |
| conflict-on-save | 1 | pending_confirmation | pending_confirmation | True | 7 |
| save-response-lost | 1 | completed | completed | True | 6 |
| changed-slot | 1 | pending_confirmation | pending_confirmation | True | 5 |
| time-conflict | 2 | pending_confirmation | pending_confirmation | True | 5 |
| window-authorized | 2 | completed | completed | True | 5 |
| conflict-on-save | 2 | pending_confirmation | pending_confirmation | True | 7 |
| save-response-lost | 2 | completed | completed | True | 6 |
| changed-slot | 2 | pending_confirmation | pending_confirmation | True | 5 |
| time-conflict | 3 | pending_confirmation | pending_confirmation | True | 5 |
| window-authorized | 3 | completed | completed | True | 5 |
| conflict-on-save | 3 | pending_confirmation | pending_confirmation | True | 7 |
| save-response-lost | 3 | completed | completed | True | 6 |
| changed-slot | 3 | pending_confirmation | pending_confirmation | True | 5 |

Each case has input, actual output, JSONL trace and SQLite snapshot beside this report.
`summary.json` binds artifacts to SHA-256 hashes and the exact source used.
