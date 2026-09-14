# Results

| Directory | Contents |
|---|---|
| [`baselines/`](baselines/README.md) | Reference scores: five frontier models over 30 days, and the scripted operator over both horizons |
| [`validation/`](validation/README.md) | Integration checks that the Harbor task and the in-process reference agree to the cent |

> [!NOTE]
> **Awaiting a v3 batch.** Benchmark v3.0.0 changed the working day, the cash
> handling and the tool-call cost, which invalidates every model trial and
> validation run recorded before it. Their raw artifacts have been cleared and
> their summary tables kept, marked as v2, until the batch is re-run. Only the
> scripted reference under [`baselines/scripted-reference/`](baselines/scripted-reference/README.md)
> is current.

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
