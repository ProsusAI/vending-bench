"""Reproduce the scripted calibration, explicitly separate from model results.

Covers the default 30-day challenge and the full-year variant, five seeds each.
Writes results/baselines/scripted-reference/{results.json,README.md}.

    python3 scripts/run_baselines.py            # both horizons
    python3 scripts/run_baselines.py --days 30  # the default challenge only
"""
import argparse
import json
from pathlib import Path
import time
import sync_config
from local_run import Engine, Bot, DirectClient, config

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ROOT / 'tasks/vending-bench/environment/sim-server/variants'
SEEDS = (1, 2, 3, 4, 5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, nargs='+', default=[30, 365],
                    help='horizons to calibrate (default: 30 365)')
    ap.add_argument('--seeds', type=int, nargs='+', default=list(SEEDS))
    ap.add_argument('--output', type=Path, default=ROOT / 'results/baselines/scripted-reference')
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    rows = []
    for days in args.days:
        # 30 days is the shipped default; the year is a config-only variant.
        variant = VARIANTS / 'full-year.toml' if days == 365 else None
        for seed in args.seeds:
            config.reload(variant, environ={'VENDING_SIM_DAYS': str(days)})
            engine = Engine.new(seed)
            started = time.monotonic()
            Bot(DirectClient(engine), sync_config.world()).run()
            rows.append({**engine.score(finalize=True),
                         'runtime_seconds': round(time.monotonic() - started, 3),
                         'agent': 'scripted reference with privileged economic parameters',
                         'api_cost_usd': 0.0})
    config.reload()

    (args.output / 'results.json').write_text(json.dumps(rows, indent=2))
    lines = [
        '# Scripted reference calibration', '',
        'This bot has catalogue economics supplied from the simulator. It is a calibration '
        'reference, not an AI model result. It ignores marketing and customer refunds; it '
        'checks prices and buys stock. Runtime is in-process, without MCP or model latency.', '',
        'These numbers are the baseline to beat. **30 days is the default challenge**; the '
        '365-day rows come from `variants/full-year.toml`. Never compare scores across horizons.', '',
        '| Days | Seed | Completed | Final balance | Profit | Reward | Tool calls |',
        '|---:|---:|---|---:|---:|---:|---:|',
    ]
    for r in rows:
        lines.append(f'| {r["sim_days"]} | {r["seed"]} | {r["completed"]} | '
                     f'€{r["final_balance"]:,.2f} | €{r["profit"]:,.2f} | '
                     f'€{r["reward"]:,.2f} | {r["tool_calls"]} |')
    lines += ['', '## Summary', '',
              '| Days | Seeds | Completed | Mean reward | Min | Max |',
              '|---:|---:|---|---:|---:|---:|']
    for days in args.days:
        group = [r for r in rows if r['sim_days'] == days]
        if not group:
            continue
        rewards = [r['reward'] for r in group]
        lines.append(f'| {days} | {len(group)} | {sum(r["completed"] for r in group)}/{len(group)} | '
                     f'€{sum(rewards) / len(rewards):,.2f} | €{min(rewards):,.2f} | €{max(rewards):,.2f} |')
    (args.output / 'README.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
