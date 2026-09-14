"""Generate a shareable report from real per-run results; never invent costs."""
import argparse
import json
from pathlib import Path
import statistics
from collections import defaultdict

ROOT=Path(__file__).resolve().parents[1]


def report(directory):
    groups=defaultdict(list)
    for path in sorted(directory.glob('*/result.json')):
        row=json.loads(path.read_text()); groups[(row['model_requested'],row['horizon_days'],row['benchmark_sha256'])].append((path,row))
    lines=['# Prosus Vending Bench — actual model runs','',
        'These are live Claude CLI runs through the 22 MCP business tools. Built-in tools are disabled. '
        'Each run starts from empty machines with €1,500. Seeds are simulation seeds; model sampling remains stochastic.', '',
        'Incomplete, interrupted and bankrupt runs receive zero. Score statistics include those zeroes. '
        'API cost is the CLI’s reported API-equivalent estimate, not a claim about subscription charges. '
        'Unknown costs are shown as unavailable; they are never counted as zero.', '',
        '| Model requested | Days | Runs | Completed | Score mean ± sample SD | Score range | Mean runtime | Reported API cost |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for (model,days,sha),pairs in groups.items():
        rows=[r for _,r in pairs]; rewards=[r['reward'] for r in rows]
        costs=[r['api_cost_usd'] for r in rows]
        cost=f'${sum(costs):.2f} total' if all(c is not None for c in costs) else 'unavailable for some runs'
        lines.append(f'| {model} | {days} | {len(rows)} | {sum(r["completed"] for r in rows)}/{len(rows)} | '
            f'€{statistics.mean(rewards):,.2f} ± €{statistics.stdev(rewards) if len(rows)>1 else 0:,.2f} | '
            f'€{min(rewards):,.2f}–€{max(rewards):,.2f} | {statistics.mean(r["runtime_seconds"] for r in rows)/60:.1f} min | {cost} |')
    lines += ['', 'Resolved model identifiers: ' + '; '.join(sorted({key for pairs in groups.values() for _, r in pairs for key in r.get('model_usage', {})})) + '.', '']
    lines+=['','## Individual runs','','| Run | Completed | Day reached | Balance | Score | Runtime | API cost | Outcome |',
            '|---|---|---:|---:|---:|---:|---:|---|']
    for pairs in groups.values():
        for path,r in pairs:
            cost=f'${r["api_cost_usd"]:.4f}' if r['api_cost_usd'] is not None else 'unavailable'
            lines.append(f'| [{path.parent.name}]({path.parent.name}/result.json) | {r["completed"]} | {r.get("days_operated","—")} | '
                f'€{r.get("final_balance",0):,.2f} | €{r["reward"]:,.2f} | {r["runtime_seconds"]:.1f}s | {cost} | {r.get("error") or r.get("termination_reason") or r.get("model_result_subtype") or "finished"} |')
    lines+=['','## Provenance','', 'The exact evaluation source is archived in `evaluation-source.zip`, with its fingerprint in `evaluation-source.sha256`. Each run directory includes the effective configuration, briefing, model usage, '
        'harness version, source fingerprint, terminal trajectory, and raw CLI output. Model aliases are resolved '
        'in `model_usage`; use the resolved identifier when reproducing a run. Different fingerprints are grouped separately.', '',
        'The default 30-day challenge tests startup and early operations. It is not evidence of year-long coherence, '
        'and its balances must not be compared with full-year runs. A handful of seeds per model is exploratory, not a statistically robust ranking.', '']
    (directory/'README.md').write_text('\n'.join(lines))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('directory',type=Path)
    report(ap.parse_args().directory)
