# Release validation

> [!IMPORTANT]
> **Superseded by benchmark v3.0.0.** These runs were recorded under the v2
> rules: a 07:00–23:00 day, per-machine cash boxes with a `collect_cash` errand,
> a 25-minute restock, and a €0.12-per-tool-call compute levy charged to the
> business. v3 runs an 09:00–17:00 working day, banks cash overnight, charges 45
> minutes to fill a machine and 30 to swap a slot, and charges the business
> nothing for tool calls. The numbers below are **not comparable** with a v3 run
> and have not been rewritten. The table is kept as the record of what v2
> validated; **the raw job, score and test JSON has been cleared** and these
> checks will be re-run against v3.

Integration checks that the shipped Harbor task produces the same world and the
same score as the in-process reference. These are scripted checks, not model
performance results — for those see [`../baselines/`](../baselines/README.md).

| Check | Horizon | Result |
|---|---:|---|
| Harbor oracle, default task | 30 days | Completed, €3,219.92 — matches the in-process reference to the cent |
| Harbor oracle, `full-year.toml` | 365 days | Completed, €87,488.56 — matches the in-process reference to the cent |

- 78 engine and HTTP/MCP transport tests pass on Python 3.12.
- Harbor reported zero trial exceptions for both runs.
- The verifier's final checks validate score consistency. A completed run that
  loses money is a valid benchmark outcome, not a failure.

The 30-day transport check ran before the verifier assertions were simplified to
validate the scoring contract; the 365-day check used the final verifier.
Neither change affects the simulation reward.

## Files

Nothing here yet. Re-running these checks against v3 writes back, per horizon,
`harbor-<horizon>-job.json` (Harbor job stats),
`harbor-<horizon>-score.json` (the sealed verifier score) and
`harbor-<horizon>-tests.json` (the verifier's test results).
