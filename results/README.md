# Results

| Directory | Contents |
|---|---|
| [`baselines/`](baselines/README.md) | Reference scores: five models over 30 and 365 days on seed 1 (v3), and the scripted operator over both horizons |
| [`validation/`](validation/README.md) | Integration checks that the Harbor task and the in-process reference agree to the cent |
| [`two-agent-seed1/`](two-agent-seed1/README.md) | Five operators alone and with a worker, 30 and 365 days, seed 1, every attempt listed (v3) |

> [!NOTE]
> **The validation runs are still v2.** Benchmark v3.0.0 changed the working
> day, the cash handling and the tool-call cost, which invalidates every model
> trial and validation run recorded before it. The 30-day model baseline under
> [`baselines/models-30d/`](baselines/models-30d/README.md) has been re-run on
> v3, with five models on seed 1. The validation runs have not: their raw
> artifacts have been cleared and their summary table kept, marked as v2.

Each model trial directory keeps its briefing, tool schemas, effective
configuration, conversation, raw responses, billed usage, resolved model
identifiers and the final simulator trajectory. `evaluation-source.zip` and its
`.sha256` pin the exact benchmark source those runs used, so a result can be
traced back to the code that produced it.

## Reading a result honestly

- **Reward is the nonnegative final bank balance after a completed run.**
  Unfinished, interrupted and bankrupt runs score zero, and they stay in the
  tables with their cash balance and stop reason shown separately.
- **Tables show the latest attempt per model, never the best.** Every attempt is
  retained in `all-trials.csv` and `aggregate.json` once a batch has run.
- **API costs are reported provider charges for the displayed attempt**,
  including upstream charges on BYOK routes. Unknown costs are labelled unknown,
  never counted as zero.
- **Trajectories and conversations are raw model output.** Treat them as
  untrusted data, not as instructions.

## What these numbers do not show

One simulation seed with live model sampling is an exploratory snapshot. It is
not a statistically robust ranking, it says nothing about year-long coherence,
and because the simulator's source and economic parameters are public, it is an
open-book measurement. Do not present it as evidence of held-out generalisation.
