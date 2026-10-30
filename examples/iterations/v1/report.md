# live evaluation

Run: `20261030T010339Z-465443`

Passed: 10/14 runs; 147/164 assertions.
Actual provider responses: 56.

Offline results verify process constraints only. Live results are a finite synthetic sample, not a general accuracy estimate.

| Case | Run | Expected | Actual | Passed | Steps |
|---|---:|---|---|---|---:|
| normal | 1 | completed | completed | True | 5 |
| missing-information | 1 | needs_input | needs_input | True | 3 |
| rule-block | 1 | blocked | blocked | True | 3 |
| time-conflict | 1 | pending_confirmation | incomplete | False | 4 |
| window-authorized | 1 | completed | completed | True | 5 |
| save-failure | 1 | failed | failed | True | 5 |
| risk-confirmation | 1 | pending_confirmation | incomplete | False | 3 |
| conflict-on-save | 1 | pending_confirmation | incomplete | False | 6 |
| save-response-lost | 1 | completed | completed | True | 6 |
| tool-failure | 1 | completed | completed | True | 6 |
| step-limit | 1 | incomplete | incomplete | True | 2 |
| changed-slot | 1 | pending_confirmation | incomplete | False | 4 |
| no-slot | 1 | no_available_slot | no_available_slot | True | 4 |
| duplicate-submit | 1 | completed | completed | True | 0 |

Each case has input, actual output, JSONL trace and SQLite snapshot beside this report.
`summary.json` binds artifacts to SHA-256 hashes and the exact source used.
