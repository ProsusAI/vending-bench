# Baselines

Reference numbers for the **default 30-day challenge** and the 365-day variant.
Use them to sanity-check your own runs; do not treat them as a definitive model
ranking.

| Baseline | What it is | Horizon | Benchmark version |
|---|---|---|---|
| [`models-30d/`](models-30d/README.md) | Five frontier models through the MCP-only runner, one batch, identical conditions | 30 days | **v2 — superseded** |
| [`scripted-reference/`](scripted-reference/README.md) | The scripted operator, five seeds, both horizons | 30 and 365 days | v3 |

**Never compare across benchmark versions either.** v3 shortened the day to
09:00–17:00, replaced the cash boxes with overnight banking, made filling a
machine a 45-minute errand, added a 30-minute `swap_item`, and stopped charging
the business for tool calls. The model batch predates all of that and has not
been re-run; it is kept as a record, not as a current leaderboard.

**Never compare across horizons.** A 30-day balance and a 365-day balance
measure different things. Keep them in separate tables.

**Never compare across harnesses.** The model baseline uses the MCP-only
OpenRouter runner, where the agent has exactly the 22 business tools and nothing
else. A Harbor run with broader tool permissions, or a Claude CLI run, is a
different experiment.

## The scripted reference is not a model result

The scripted operator reads catalogue economics straight from the simulator: it
knows every true cost and elasticity without having to discover them. It also
ignores marketing and customer complaints entirely. It is a calibration floor
for a competent, well-informed operator, not a score any model is competing
with. Reproduce it with:

```bash
python3 scripts/run_baselines.py
```

## Reproducing the model baseline

```bash
uv run --with fastmcp==2.11.3 --with httpx --with python-dotenv \
  python scripts/run_openrouter.py --env-file /path/to/.env \
  --days 30 --seeds 1 --total-budget 0 --timeout 0 --max-turns 0 \
  --models anthropic/claude-opus-5 openai/gpt-5.6-sol \
           anthropic/claude-sonnet-5 openai/gpt-5.6-luna \
           anthropic/claude-haiku-4.5 \
  --output results/my-baseline

uv run --with matplotlib python scripts/build_leaderboard.py \
  --batch results/my-baseline
```

Live model sampling is not deterministic, so your numbers will differ. The
simulation itself is deterministic given a seed. One seed is exploratory; if you
want a claim that holds, run several seeds and report the spread.
