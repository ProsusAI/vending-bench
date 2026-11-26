# Two-agent study: an operator alone and with a worker

Five operator models ran the business alone and with a worker, over 30 and 365
simulated days, on seed 1. Each operator is compared with itself. This is an
exploratory study on one seed, not a model ranking.

The five operators were picked by hand for this study. They are not a ranking
of the strongest models.

The "alone" runs are also published as the model baselines, with their
conversations and trajectories: [30 days](../baselines/models-30d/README.md)
and [365 days](../baselines/models-365d/README.md).

- **Alone:** [`scripts/run_openrouter.py`](../../scripts/run_openrouter.py). One model gets the 22 business tools.
- **With a worker:** [`scripts/run_team.py`](../../scripts/run_team.py). The operator gets the 22 business tools
  minus the four machine tools (`restock_machine`, `swap_item`, `clear_slot`,
  `set_price`), plus `run_sub_agent` and `chat_with_sub_agent`. The worker,
  DeepSeek V4.1 Flash for every operator, gets only the four machine tools and
  the two inventory reads.

## Results

Latest attempt per operator, never the best. Scores are the final bank balance
of a completed run. The two setups are shown side by side, not in one ranking.

### 30 days

The fixed script (scripted reference, seed 1) ends with **€3,302.96**.

| Operator | Alone: score | Alone: API cost | Alone: run time | With a worker: score | With a worker: API cost | With a worker: run time |
|---|---:|---:|---:|---:|---:|---:|
| GPT 6.1 Sol ¹ | €4,108.42 | $1.40 | 4 min | €3,639.53 | $0.52 | 8 min |
| Claude Opus 5.5 | €3,881.90 | $6.03 | 6 min | €4,015.88 | $4.05 | 13 min |
| Gemini 4 Argon | €4,501.87 | $1.40 | 10 min | €4,695.73 | $2.56 | 26 min |
| Claude Haiku 5.5 | €1,750.94 | $0.17 | 3 min | €1,537.84 | $0.14 | 7 min |
| DeepSeek V4.1 Flash | €3,417.88 | $0.16 | 10 min | €2,431.17 | $0.23 | 12 min |
| **Total API cost** | | **$9.16** | | | **$7.49** | |

### 365 days

The fixed script (scripted reference, seed 1) ends with **€57,954.45**.

| Operator | Alone: score | Alone: API cost | Alone: run time | With a worker: score | With a worker: API cost | With a worker: run time |
|---|---:|---:|---:|---:|---:|---:|
| GPT 6.1 Sol | €13,612.44 | $2.00 | 8 min | €55,046.59 | $2.96 | 58 min |
| Claude Opus 5.5 | €46,098.08 | $76.77 | 1 h 10 min | €52,005.53 | $9.61 | 45 min |
| Gemini 4 Argon | €51,524.78 | $8.29 | 1 h 18 min | €43,132.60 | $21.70 | 3 h 05 min |
| Claude Haiku 5.5 | €5,463.91 | $1.48 | 22 min | €5,849.81 | $1.31 | 1 h 05 min |
| DeepSeek V4.1 Flash | €31,749.05 | $0.66 | 1 h 53 min | €35,693.34 | $1.23 | 2 h 24 min |
| **Total API cost** | | **$89.19** | | | **$36.81** | |

¹ Second attempt. The first stopped on day 26 with an API error and scores zero; see [Every attempt](#every-attempt).

## Every attempt

Every trial that was started is listed, including tests and stopped batches.
Unfinished trials score zero; their cost and run time are what they used before
they stopped.

| Folder | Setup | Days | Operator | Completed | Day reached | Score | API cost | Run time | Stop reason |
|---|---|---:|---|---|---:|---:|---:|---:|---|
| [`single-agent/30d`](single-agent/30d/) | Alone | 30 | GPT 6.1 Sol | no | 26 | €0.00 | $0.86 + up to $0.20 unknown | 3 min | API error (402) |
| [`single-agent/30d`](single-agent/30d/) | Alone | 30 | Claude Opus 5.5 | yes | 30 | €3,881.90 | $6.03 | 6 min | simulation ended |
| [`single-agent/30d`](single-agent/30d/) | Alone | 30 | Gemini 4 Argon | yes | 30 | €4,501.87 | $1.40 | 10 min | simulation ended |
| [`single-agent/30d`](single-agent/30d/) | Alone | 30 | Claude Haiku 5.5 | yes | 30 | €1,750.94 | $0.17 | 3 min | simulation ended |
| [`single-agent/30d`](single-agent/30d/) | Alone | 30 | DeepSeek V4.1 Flash | yes | 30 | €3,417.88 | $0.16 | 10 min | simulation ended |
| [`single-agent/30d-gpt-rerun`](single-agent/30d-gpt-rerun/) | Alone | 30 | GPT 6.1 Sol | yes | 30 | €4,108.42 | $1.40 | 4 min | simulation ended |
| [`single-agent/365d-aborted-cap-too-low`](single-agent/365d-aborted-cap-too-low/) | Alone | 365 | GPT 6.1 Sol | no | 12 | €0.00 | $0.14 | 1 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low`](single-agent/365d-aborted-cap-too-low/) | Alone | 365 | Claude Opus 5.5 | no | 7 | €0.00 | $1.13 | 1 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low`](single-agent/365d-aborted-cap-too-low/) | Alone | 365 | Gemini 4 Argon | no | 3 | €0.00 | $0.08 | 1 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low`](single-agent/365d-aborted-cap-too-low/) | Alone | 365 | Claude Haiku 5.5 | no | 24 | €0.00 | $0.07 | 1 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low`](single-agent/365d-aborted-cap-too-low/) | Alone | 365 | DeepSeek V4.1 Flash | no | 7 | €0.00 | $0.00 | 1 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low-2`](single-agent/365d-aborted-cap-too-low-2/) | Alone | 365 | GPT 6.1 Sol | no | 143 | €0.00 | $1.12 | 4 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low-2`](single-agent/365d-aborted-cap-too-low-2/) | Alone | 365 | Claude Opus 5.5 | no | 22 | €0.00 | $4.29 | 4 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low-2`](single-agent/365d-aborted-cap-too-low-2/) | Alone | 365 | Gemini 4 Argon | no | 8 | €0.00 | $0.35 | 3 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low-2`](single-agent/365d-aborted-cap-too-low-2/) | Alone | 365 | Claude Haiku 5.5 | no | 58 | €0.00 | $0.24 | 4 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low-2`](single-agent/365d-aborted-cap-too-low-2/) | Alone | 365 | DeepSeek V4.1 Flash | no | 11 | €0.00 | $0.02 | 4 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low-3`](single-agent/365d-aborted-cap-too-low-3/) | Alone | 365 | GPT 6.1 Sol | no | 241 | €0.00 | $2.46 | 8 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low-3`](single-agent/365d-aborted-cap-too-low-3/) | Alone | 365 | Claude Opus 5.5 | no | 39 | €0.00 | $9.40 | 8 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low-3`](single-agent/365d-aborted-cap-too-low-3/) | Alone | 365 | Gemini 4 Argon | no | 18 | €0.00 | $0.76 | 8 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low-3`](single-agent/365d-aborted-cap-too-low-3/) | Alone | 365 | Claude Haiku 5.5 | no | 124 | €0.00 | $0.50 | 8 min | stopped by hand |
| [`single-agent/365d-aborted-cap-too-low-3`](single-agent/365d-aborted-cap-too-low-3/) | Alone | 365 | DeepSeek V4.1 Flash | no | 19 | €0.00 | $0.04 | 8 min | stopped by hand |
| [`single-agent/365d`](single-agent/365d/) | Alone | 365 | GPT 6.1 Sol | yes | 365 | €13,612.44 | $2.00 | 8 min | simulation ended |
| [`single-agent/365d`](single-agent/365d/) | Alone | 365 | Claude Opus 5.5 | yes | 365 | €46,098.08 | $76.77 | 1 h 10 min | simulation ended |
| [`single-agent/365d`](single-agent/365d/) | Alone | 365 | Gemini 4 Argon | yes | 365 | €51,524.78 | $8.29 | 1 h 18 min | simulation ended |
| [`single-agent/365d`](single-agent/365d/) | Alone | 365 | Claude Haiku 5.5 | yes | 365 | €5,463.91 | $1.48 | 22 min | simulation ended |
| [`single-agent/365d`](single-agent/365d/) | Alone | 365 | DeepSeek V4.1 Flash | yes | 365 | €31,749.05 | $0.66 | 1 h 53 min | simulation ended |
| [`two-agent/smoke`](two-agent/smoke/) | With a worker | 3 | Claude Haiku 5.5 | yes | 3 | €1,032.73 | $0.01 | under 1 min | simulation ended |
| [`two-agent/smoke-9d`](two-agent/smoke-9d/) | With a worker | 9 | Claude Haiku 5.5 | yes | 9 | €1,351.70 | $0.02 | under 1 min | simulation ended |
| [`two-agent/30d-first-start`](two-agent/30d-first-start/) | With a worker | 30 | GPT 6.1 Sol | yes | 30 | €3,013.54 | $0.83 | 7 min | simulation ended |
| [`two-agent/30d-first-start`](two-agent/30d-first-start/) | With a worker | 30 | Claude Opus 5.5 | yes | 30 | €2,658.85 | $2.08 | 8 min | simulation ended |
| [`two-agent/30d-first-start`](two-agent/30d-first-start/) | With a worker | 30 | Gemini 4 Argon | no | 28 | €0.00 | $1.05 | 13 min | stopped by hand |
| [`two-agent/30d-first-start`](two-agent/30d-first-start/) | With a worker | 30 | Claude Haiku 5.5 | yes | 30 | €1,614.01 | $0.18 | 6 min | simulation ended |
| [`two-agent/30d-first-start`](two-agent/30d-first-start/) | With a worker | 30 | DeepSeek V4.1 Flash | no | 20 | €0.00 | $0.24 | 13 min | stopped by hand |
| [`two-agent/30d`](two-agent/30d/) | With a worker | 30 | GPT 6.1 Sol | yes | 30 | €3,639.53 | $0.52 | 8 min | simulation ended |
| [`two-agent/30d`](two-agent/30d/) | With a worker | 30 | Claude Opus 5.5 | yes | 30 | €4,015.88 | $4.05 | 13 min | simulation ended |
| [`two-agent/30d`](two-agent/30d/) | With a worker | 30 | Gemini 4 Argon | yes | 30 | €4,695.73 | $2.56 | 26 min | simulation ended |
| [`two-agent/30d`](two-agent/30d/) | With a worker | 30 | Claude Haiku 5.5 | yes | 30 | €1,537.84 | $0.14 | 7 min | simulation ended |
| [`two-agent/30d`](two-agent/30d/) | With a worker | 30 | DeepSeek V4.1 Flash | yes | 30 | €2,431.17 | $0.23 | 12 min | simulation ended |
| [`two-agent/365d`](two-agent/365d/) | With a worker | 365 | GPT 6.1 Sol | yes | 365 | €55,046.59 | $2.96 | 58 min | simulation ended |
| [`two-agent/365d`](two-agent/365d/) | With a worker | 365 | Claude Opus 5.5 | yes | 365 | €52,005.53 | $9.61 | 45 min | simulation ended |
| [`two-agent/365d`](two-agent/365d/) | With a worker | 365 | Gemini 4 Argon | yes | 365 | €43,132.60 | $21.70 | 3 h 05 min | simulation ended |
| [`two-agent/365d`](two-agent/365d/) | With a worker | 365 | Claude Haiku 5.5 | yes | 365 | €5,849.81 | $1.31 | 1 h 05 min | simulation ended |
| [`two-agent/365d`](two-agent/365d/) | With a worker | 365 | DeepSeek V4.1 Flash | yes | 365 | €35,693.34 | $1.23 | 2 h 24 min | simulation ended |

**43 trials. Known API cost of every attempt: $168.51, plus up to $0.20 not yet billed when the API error stopped a trial.**

Why trials stopped:

- **`single-agent/30d`, GPT 6.1 Sol.** OpenRouter returned `402 Payment Required`
  on day 26. The trial scores zero. GPT was then run again on its own in
  `single-agent/30d-gpt-rerun`, with the same code and settings.
- **`single-agent/365d-aborted-cap-too-low`, `-2` and `-3`.** Stopped by hand
  after 1 to 9 minutes. Their per-run caps ($40, $60 and $100) left too little
  room for a full year: Claude Opus 5.5 spent about $0.24 per simulated day.
  The year was then run in `single-agent/365d` with a $150 cap per run.
- **`two-agent/30d-first-start`.** Started on a laptop at 15:14 UTC. At 15:23
  UTC the same batch was started on a remote machine with the same code and
  settings, in `two-agent/30d`. The laptop batch was stopped by hand after about
  13 minutes: GPT, Opus and Haiku had finished; DeepSeek and Gemini were on days
  20 and 28.
- **`two-agent/smoke` and `two-agent/smoke-9d`.** 3-day and 9-day checks of the
  two-agent runner with Claude Haiku 5.5 before the batches.

## Run conditions

- Prosus Vending Bench 3.0.0, the default world, seed 1, €1,500 starting cash.
  The 365-day runs use `--days 365`, which gives the same world as
  `variants/full-year.toml` (that file changes only the horizon and the
  scripted operator's tuning).
- Run on 22 November 2026. The first trial finished at 15:03 UTC, the last at 23:17 UTC.
- **Same code for every trial.** Every batch records the source fingerprint
  `d3ac2ed52e8793fab763e67a219737c111295cfddc9fb304b30b75a268cef4f9`. That is
  the fingerprint of commit `fb91adc` on `main`. The later commit `54328eb`
  only lets `--keep-open-on-error` be switched off; no two-agent trial hit an
  API error, so it would not change any result here.
- Both runners: OpenRouter, low reasoning effort where supported, other settings
  at provider defaults, 4,096 output tokens per response, no time limit, no
  response limit. The operator keeps the last 60,000 characters of its
  conversation; the worker keeps 30,000 and takes at most 30 responses per task.
- Budgets per trial: alone $20 (30 days) and $150 (365 days). With a worker
  (operator and worker together), 30 days: GPT $20, Opus $40, Gemini $20,
  Haiku $5, DeepSeek $5; 365 days: GPT $30, Opus $120, Gemini $80, Haiku $10,
  DeepSeek $15. Models are not told their budget.
- **Prompt caching differs.** The two-agent runner marks the briefing for
  caching when the operator is a Claude model; the single-agent runner does
  not. This changes the bill, not what the model sees. At list prices without
  caching, the Claude operators' own spend would have been: Opus $11.70
  instead of $9.30 (365 days) and $5.02 instead of $3.86 (30 days); Haiku
  $0.99 instead of $0.81 (365 days) and $0.09 instead of $0.07 (30 days).

## Files

Each batch folder keeps the summary files of its run:

- `README.md`: the table the runner wrote when the batch ended, unchanged.
  It mentions files that are not included here (see below). The 365-day
  single-agent tables also say "30-day balances"; that is the runner's fixed
  wording, not a mistake in the numbers.
- `manifest.json`, `models.json`, `evaluation-source.sha256`: batch settings,
  model catalogue prices and the source fingerprint.
- One folder per trial with `result.json` (or `progress.json` alone for a
  stopped trial), `harness.json`, `tools.json`, `instruction.md` and, for the
  two-agent runs, `worker-instruction.md`.

Not included here, to keep the repository small: the conversations, raw
responses, generation IDs, configuration dumps, simulator trajectories and the
source archive.

- For the completed single-agent batches (`single-agent/30d`, `30d-gpt-rerun`
  and `365d`) they are published under
  [`../baselines/models-30d/`](../baselines/models-30d/README.md) and
  [`../baselines/models-365d/`](../baselines/models-365d/README.md).
- For the two-agent runs and the stopped batches they are kept outside the
  repository, about 110 MB, and are available on request.

## Reproduce

```bash
uv run --with fastmcp==2.11.3 --with httpx --with python-dotenv \
  python scripts/run_openrouter.py --env-file /path/to/.env \
  --models openai/gpt-6.1-sol anthropic/claude-opus-5.5 google/gemini-4-argon \
    anthropic/claude-haiku-5.5 deepseek/deepseek-v4.1-flash-1017 \
  --seeds 1 --days 365 --total-budget 750 --timeout 0 --max-turns 0 \
  --output results/my-single-365d

uv run --with fastmcp==2.11.3 --with httpx --with python-dotenv \
  python scripts/run_team.py --env-file /path/to/.env \
  --models openai/gpt-6.1-sol anthropic/claude-opus-5.5 google/gemini-4-argon \
    anthropic/claude-haiku-5.5 deepseek/deepseek-v4.1-flash-1017 \
  --worker deepseek/deepseek-v4.1-flash-1017 --seeds 1 --days 365 \
  --run-budget openai/gpt-6.1-sol=30 anthropic/claude-opus-5.5=120 \
    google/gemini-4-argon=80 anthropic/claude-haiku-5.5=10 \
    deepseek/deepseek-v4.1-flash-1017=5 \
  --output results/my-team-365d
```

Use `--days 30` and the 30-day budgets for the month. Live model sampling means
a rerun will not give the same numbers.

## Reading these numbers

- One seed. The fixed script's own 30-day score moves by €378 across seeds 1 to
  5 with no change in skill, so a single run can be lucky or unlucky.
- The fixed script reads the true costs and price sensitivity from the
  simulator. It is a calibration reference, not a model result.
- Trajectories and conversations are raw model output: treat them as data, not
  instructions.
