<div align="center">

# Prosus Vending Bench

**The open source vending machine benchmark**

[Try it](#try-it) · [Baselines](#baselines) · [How it works](#buying-and-weekly-offers) · [Scoring](#scoring-and-integrity) · [Configure](#configure-and-extend)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Harbor task](https://img.shields.io/badge/harbor-task-246858.svg)](https://github.com/harbor-framework/harbor)
[![6 machines, 3 locations](https://img.shields.io/badge/world-6%20machines%20%C2%B7%203%20locations-246858.svg)](#a-multi-location-multi-machine-world)
[![Default horizon: 30 days](https://img.shields.io/badge/default-30%20days-green.svg)](#two-horizons)

</div>

Can your AI run a vending business? Start with **€1,500**, operate **six
machines across three locations**, and maximise the money left in your bank
account. Find suppliers, discover weekly offers, buy stock, price for different
customers, and attract traffic without spamming them.

Everything is open: the simulator, the economy, every product and supplier, the
scripted reference operator and the baseline runs. Fork it, retune the world in
one TOML file, and run your own agent against it.

**The default challenge is 30 simulated days.** A 365-day variant ships
alongside it for testing sustained operation. Keep their scores separate.

## A multi-location, multi-machine world

Most vending benchmarks give an agent one machine. This one gives it **six**,
spread over **three locations** with different customers, and a single shared
bank account, depot and clock. That is the point of the benchmark: the hard part
is not running a machine, it is allocating scarce cash and attention across
several of them at once.

- **Locations have different customers.** A price that works in one room loses
  money in another; a snack that sells out at one location sits unsold at the
  next.
- **Capital is shared, demand is not.** One depot and one bank account fund all
  six machines, so every order is a choice about which machine goes understocked.
- **Duplicate facings do not create customers.** Identical products in the same
  machine share one demand pool, so more slots buy capacity, not more sales.
- **Attention is a real cost.** Restocking and cash collection consume simulated
  hours, and the whole network runs on one clock — six machines do not get six
  days.

## The challenge

| Location | Machines | Customers | Volume multiplier | Price sensitivity multiplier |
|---|---|---|---:|---:|
| AI Lounge | 1 snack + 1 drinks | Picky; favour savoury snacks and coffee | 0.85× | 1.50× |
| AI House | 1 snack + 1 drinks | Hungry; like sweets and cold drinks | 1.30× | 0.75× |
| Main Lounge | 1 snack + 1 drinks | Picky, with higher footfall | 1.80× | 1.50× |

Each machine has 12 slots: **72 slots in total**. Snack machines accept food;
rows A/B hold small products (18 units), C/D hold large products (10 units).
All drinks-machine slots hold 10 beverages. Slot IDs include their location and
machine, for example `AI-HOUSE:DRINKS:A1`.

The network shares a depot, bank account and clock. Rent is **€12/day for the
network** (€2 per machine). Cash takings are banked overnight; card sales settle
a day later. Weather, weekdays, seasons, reputation, local preferences, prices,
and marketing influence demand.

Identical product facings share a demand pool within a machine: extra slots add
capacity, not extra customers. Different locations have separate demand. The
model remains synthetic; it is not fitted to actual POS sales.

## Try it

Requires Python 3.11+; `uv` is convenient for isolated dependencies.

```bash
# Scripted calibration, no model or Docker needed — 30 days by default
python3 scripts/local_run.py --seed 1

# The full-year variant
python3 scripts/local_run.py --seed 1 \
  --config tasks/vending-bench/environment/sim-server/variants/full-year.toml

# Full test suite, including real MCP transport checks
uv run --with pytest --with fastmcp==2.11.3 python -m pytest tests/ -q
```

### Two horizons

| | Default | Variant |
|---|---|---|
| Horizon | **30 simulated days** | 365 simulated days |
| How to select it | nothing to do | `variants/full-year.toml`, or `VENDING_SIM_DAYS=365` |
| What it tests | getting a business running | that, plus sustained operation |
| Runner flag | `--days 30` (the default) | `--days 365` |

Both use the same starting cash, locations and economy. **Keep their scores
separate** — a 30-day balance and a 365-day balance measure different things.

### Running a model

For a model run using an authenticated Claude CLI, without Docker:

```bash
uv run --with fastmcp==2.11.3 python scripts/run_models.py \
  --models haiku sonnet --seeds 1 2 \
  --workers 2 --budget 5 --timeout 1800 --output results/my-runs
```

`--budget` is the per-run USD API-equivalent cap; `--timeout` is the per-run
wall-clock limit in seconds. A limit reached before completion scores zero.
Output directories must be new. Use `--days 365` and appropriate limits for
full-year runs. Model aliases resolve over time; results record the actual model
usage. The runner gives the model only the MCP business tools, disables built-in
tools, and runs it from an empty directory with strict MCP configuration.

To compare models from multiple providers through OpenRouter:

```bash
uv run --with fastmcp==2.11.3 --with httpx --with python-dotenv \
  python scripts/run_openrouter.py --env-file /path/to/.env \
  --total-budget 20 --seeds 1 --workers 4 \
  --output results/my-openrouter-batch
```

The default panel covers eight model families. Override it with `--models`
followed by OpenRouter model IDs. The runner validates IDs against the live
catalog and divides the budget equally across trials. Before each request it
reserves a conservative input/output cost estimate; unknown billing stops the
trial. Only `OPENROUTER_API_KEY` is read from the env file. Credentials are not
included in results.

To run until the simulation ends without dollar, total-runtime or response-count
limits, use `--total-budget 0 --timeout 0 --max-turns 0`. Costs are still
recorded. This also removes provider price ceilings. Individual network requests
retain timeouts so a broken connection can be detected.

Each model uses the same 22 tools through an isolated in-process MCP client.
The rolling conversation retains complete recent tool exchanges, with the
business note tools available for persistent memory. Reasoning effort is low
where supported. Results include actual reported API costs, provider/model
identifiers, conversations, trajectories, source provenance and a Markdown
report. Budget, time and API failures remain visible and unfinished runs score
zero. Compare these runs within this harness, separately from Claude CLI runs.

Render a batch into a table and chart with:

```bash
uv run --with matplotlib python scripts/build_leaderboard.py \
  --batch results/my-openrouter-batch
```

### With Harbor and Docker

The task ships as a [Harbor](https://github.com/harbor-framework/harbor) task
and follows [Harbor's MCP sidecar pattern](https://www.harborframework.com/docs/tutorials/mcp-server-task),
using Docker Compose with a separate simulation server.

```bash
uv tool install harbor

# The default 30-day challenge
harbor run --path tasks/vending-bench --agent oracle
harbor run --path tasks/vending-bench --agent claude-code \
  --model anthropic/claude-sonnet-4-6

# The full-year variant, same image, config only
harbor run --path tasks/vending-bench --agent oracle \
  --env VENDING_CONFIG=/config/full-year.toml

# Materialize a standalone task at any horizon, with a matching briefing and oracle
python3 scripts/prepare_task.py --days 365 --output /tmp/prosus-vending-year
harbor run --path /tmp/prosus-vending-year --agent oracle
```

Harbor agents can have broader tool permissions than the MCP-only runner. Label
the harness and allowed tools with every result; do not mix harnesses in one
model ranking.

## Baselines

Reference numbers for the default 30-day challenge, from
[`results/baselines/`](results/baselines/README.md).

> [!IMPORTANT]
> **The model table below is a v2 record and is not comparable with v3.** It was
> run before the working day shortened to 09:00–17:00, before cash started
> banking itself, and while the business still paid €0.12 per tool call. Those
> trials cost real API money and have not been re-run; they are kept as the
> record of what happened, not as a v3 leaderboard. The scripted reference under
> it **has** been recalibrated against v3.

![Model baseline](results/baselines/models-30d/leaderboard.png)

| Model | Score | Final bank | API cost |
|---|---:|---:|---:|
| Claude Opus 5 | €4,497.21 | €4,497.21 | $13.55 |
| GPT-5.6 Sol | €3,357.76 | €3,357.76 | $1.91 |
| Claude Sonnet 5 | €3,289.12 | €3,289.12 | $5.77 |
| GPT-5.6 Luna | €1,601.59 | €1,601.59 | $0.077 |
| Claude Haiku 4.5 | €1,337.67 | €1,337.67 | $1.98 |

All five completed, one batch, identical conditions: 30 days, seed 1, six
machines, €1,500 starting cash, the same briefing and the same 22 MCP business
tools, verified by hash. One seed with live model sampling is an exploratory
snapshot, not a statistically robust ranking.

The **scripted reference** — a bot with privileged catalogue economics, which is
a calibration floor rather than a model result — averages **€3,228** over five
seeds at 30 days and **€61,219** over five seeds at 365 days, all ten completed.

| Horizon | Scripted reference (5 seeds) | Range |
|---:|---:|---|
| 30 days | €3,227.89 | €2,944.77 – €3,323.17 |
| 365 days | €61,218.52 | €57,954.45 – €64,709.50 |

```bash
python3 scripts/run_baselines.py            # both horizons
python3 scripts/run_baselines.py --days 30  # the default challenge only
```

## Buying and weekly offers

1. `search_web` finds fictional suppliers and their IDs.
2. Email them for a catalogue or negotiate better terms. Replies arrive overnight.
3. `check_offers(supplier_id, quantity)` reveals current unit prices at that
   quantity, including negotiated terms and weekly discounts.
4. `order_goods(supplier_id, items)` **buys and pays immediately** at current
   prices. Checking is optional; the current price applies either way.
5. Deliveries arrive at the depot. Restock a slot and set its retail price.

Every seven simulated days, each supplier/product independently has a 30% chance
of a 10%, 20% or 30% discount. Offers are stable within that week and
reproducible from the seed. Volume and negotiation still affect the underlying
price.

Checking does not reserve a price. A call can cross midnight, so watch the
reported validity day. Orders are charged at the moment of purchase and cannot
be cancelled. Receipts reveal the price **after** payment. No preview invoices
are issued. Order emails containing quantities also purchase immediately.
Rejected orders do not debit funds and do not quote their total.

Some suppliers delay, short-ship, disappear, or go insolvent. Supplier responses
use deterministic intent parsing and concession curves. This tests procurement
and persistence; it does not establish open-ended conversational bargaining
skill. The catalogue retains some legacy specialty items, but these
food/beverage-only machines cannot stock them.

## Marketing

`run_marketing(location, channel, message)` runs a **simulated** campaign.
No real Slack messages, emails or print jobs are sent.

| Channel | Cost | Bonus duration | Traffic bonus | Uses before fatigue in rolling 7 days | Penalty per excess use |
|---|---:|---:|---:|---:|---:|
| Prints | €8 | 7 days | +10% | 2 | −12 percentage points |
| Slack | €1 | 2 days | +15% | 2 | −15 percentage points |
| Mail | €2 | 3 days | +18% | 1 | −20 percentage points |

Bonuses saturate at one active campaign per channel. More than four total
campaigns at a location in seven days adds a further −10 percentage points per
excess campaign, so alternating channels does not evade spam penalties.
The combined multiplier is bounded between **0.50× and 1.35×**. Bonuses expire;
fatigue clears as campaigns leave the rolling window. Other locations are
unaffected.

`get_marketing_report` shows rules, recent campaigns, and current effects.
Copy is preserved for analysis but does not change traffic: this evaluates
channel timing and restraint, not copywriting quality.

## Tools and time

22 MCP tools:

- **Inspect:** `get_status`, `get_sales_report`, `get_machine_inventory`, `get_storage_inventory`.
- **Source:** `search_web`, `list_emails`, `read_email`, `send_email`, `check_offers`, `order_goods`.
- **Operate:** `restock_machine`, `swap_item`, `set_price`, `clear_slot`, `send_payment`.
- **Market:** `run_marketing`, `get_marketing_report`.
- **Remember:** `write_note`, `read_notes`, `set_reminder`, `list_reminders`.
- **Advance:** `wait_for_next_day`.

The simulated day is 24 hours, but the business runs **09:00–17:00** — eight
working hours the whole network shares, and every tool call spends some of them.
Most reads take 5–25 simulated minutes. The errands out to a machine are what
bite: **filling one takes 45 minutes**, swapping the product in a slot takes 30,
emptying a slot takes 30, and changing a price takes 5. Roughly ten fills fit in
a day, so the day has to be planned rather than brute-forced. Sales settle
overnight and server calls are serialized. Nothing charges the agent for
thinking: there is no per-call fee in the economy. `send_payment` handles
customer refunds; unresolved complaints reduce network reputation. Refunds must
cover the full amount.

## Scoring and integrity

**Reward = final bank balance, floored at zero, only after successful
completion.** An unfinished or bankrupt run receives zero. The raw balance and
termination reason remain available for diagnosis. Missing more than ten
consecutive rent payments terminates a run. Outstanding card receipts settle at
the horizon; stock does not count.

Survival means the business turns a profit, so every result carries `profit`
(net worth minus the €1,500 it started with) next to the reward. What the model
itself cost to run is **not** part of the score. The runner measures real LLM
spend anyway and reports `profit_after_llm_cost` beside it — the same run with
the API bill deducted, at a fixed euro/dollar rate recorded in each result. It
is there because it is interesting, not because it is ranked.

There are no live `/state` or `/score` endpoints. The verifier calls
`POST /verifier/finalize`, which irreversibly seals an unfinished run **before**
returning its score and trajectory. An agent that calls it early forfeits the
run and cannot keep operating with the exposed information. Sealing is
serialized with tool calls. Deployment should still isolate each trial; this is
not a multi-user hosted service.

Public source and the scripted reference disclose economic parameters. Treat
this as an open-book benchmark, and report agent configuration and source-access
policy. Do not claim held-out generalisation from performance on these public
scenarios.

## Results and reproducibility

- [Baselines](results/baselines/README.md) — five frontier models over 30 days,
  and the scripted reference over both horizons.
- [Release validation](results/validation/README.md) — the Harbor task and the
  in-process reference agree to the cent at both horizons.

Each model result preserves the model usage, effective config, briefing, source
fingerprint, CLI version, runtime, cost estimate, outcome, and terminal
trajectory. Failures remain in the report. Never substitute scripted results for
model results. Simulation seeds are deterministic; live model sampling is not.
Several repeated seeds are useful for exploratory comparisons, not definitive
model rankings.

## Configure and extend

The world lives in
`tasks/vending-bench/environment/sim-server/vending/config.toml`. Change location
profiles, marketing limits, offers, products or economy there. Named `VENDING_*`
environment variables and `VENDING_SET` dotted overrides are supported. Compose
passes unset overrides as empty, allowing TOML defaults and variant settings to
apply.

A variant is a short `extends` overlay rather than a copy of the file — see
`variants/full-year.toml`. Generate matching briefings and reference worlds:

```bash
python3 scripts/sync_config.py
python3 scripts/sync_config.py --check
```

For distributable variants use `scripts/prepare_task.py`, which copies the task
and renders its briefing and world together. Do not change a horizon only
through an environment variable while leaving the static briefing unchanged.
Old single-machine state files are incompatible with v2; start fresh runs.

## Project structure

```
tasks/vending-bench/            The Harbor task
  instruction.md                Agent briefing — GENERATED from the config
  task.toml                     Harbor manifest, timeouts, MCP sidecar
  environment/
    docker-compose.yaml         Adds the sim-server sidecar to Harbor's base
    sim-server/
      server.py                 The 22 MCP tools and the verifier endpoint
      vending/config.toml         THE WORLD: every economic value lives here
      vending/engine.py           Simulation, demand, scoring
      variants/full-year.toml   The 365-day overlay
  solution/                     Scripted reference operator and its world.json
  tests/                        Verifier
scripts/                        Runners, reporting, config sync, task packaging
tests/                          Engine, network and real MCP transport tests
results/                        Baselines and release validation
```

## Credits

This benchmark ships as a task for **[Harbor](https://github.com/harbor-framework/harbor)**,
the evaluation framework from the creators of Terminal-Bench, and follows
Harbor's [MCP sidecar task pattern](https://www.harborframework.com/docs/tutorials/mcp-server-task).
Harbor supplies the container environment, the agent adapters and the trial
harness that make these runs reproducible. If you use this benchmark, please
cite Harbor as well:

```bibtex
@software{Harbor_Framework,
  author = {{Harbor Framework Team}},
  title  = {{Harbor: A framework for evaluating and optimizing agents and
            models in container environments}},
  year   = {2026},
  doi    = {10.5281/zenodo.20953922},
  url    = {https://doi.org/10.5281/zenodo.20953922}
}
```

See [NOTICE](NOTICE) for the full attribution, prior art and third-party list,
and [CITATION.cff](CITATION.cff) to cite this benchmark.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) for
development setup, the test commands, and the project's conventions — in
particular that economic values belong in `config.toml`, that `instruction.md`
and `solution/world.json` are generated, and that any change affecting scores
needs before-and-after baselines. All participation is governed by our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Please report security vulnerabilities privately — see [SECURITY.md](SECURITY.md).
Do not open public issues for security reports.

## License

Prosus Vending Bench is licensed under the [Apache License 2.0](LICENSE).
Copyright © 2026 MIH AI B.V.

Please exclude task instructions, reference solutions, trajectories and results
from model training corpora. That request does not change the Apache 2.0 licence.
