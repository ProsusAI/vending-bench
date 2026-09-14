"""Render the model baseline for one evaluation batch: table, chart and CSV.

    uv run --with matplotlib python scripts/build_leaderboard.py
    uv run --with matplotlib python scripts/build_leaderboard.py \
        --batch results/my-batch --output results/my-batch

Reads the raw trial artifacts a runner wrote, reconciles reported API charges,
and writes README.md, leaderboard.csv, aggregate.json, leaderboard.png and
leaderboard.pdf next to them. No messages are sent anywhere.

Every trial in a batch must share one horizon, one starting balance and one
briefing; a batch that mixes run conditions is not a baseline and is rejected.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from matplotlib.transforms import Bbox
from matplotlib import font_manager

from report_openrouter import read_trial

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BATCH = ROOT / 'results/baselines/models-30d'

OUTCOMES = {
    'simulation_ended': 'Completed',
    'wall_clock_limit': 'Time limit',
    'budget_limit': 'Budget limit',
    'agent_stopped_calling_tools': 'Stopped using tools',
    'harness_or_api_error': 'API / harness error',
}


def display_name(model_id):
    """'anthropic/claude-opus-5' -> 'Claude Opus 5'. Vendor-list free."""
    slug = model_id.split('/', 1)[-1]
    words = [w.upper() if w.replace('.', '').isdigit() else w.replace('-', ' ').title()
             for w in slug.split('-')]
    return ' '.join(words).replace('Gpt', 'GPT').replace('Glm', 'GLM')


def money(value):
    return f'€{value:,.2f}'


def price(value):
    return f'${value:.3f}' if value < .10 else f'${value:.2f}'


def profit(r):
    """What the business made. The model's own running cost is not charged."""
    return r.get('profit', r['reward'] - r['starting_balance'] if r['completed'] else 0.)


def after_llm_cost(r):
    """Profit net of real LLM spend. Reported for interest, never ranked.

    Only trials that recorded their own EUR/USD rate get a figure; older
    results predate it and are shown as unknown rather than guessed at.
    """
    rate = r.get('eur_per_usd')
    if rate is None:
        return None
    return profit(r) - r['reconciled_known_cost_usd'] * rate


def load(batch):
    trials = sorted(batch.glob('*/result.json'))
    if not trials:
        raise SystemExit(f'no trials under {batch}')
    rows = [read_trial(p) for p in trials]
    for r in rows:
        r['display_name'] = display_name(r['model_requested'])
    horizons = {r['horizon_days'] for r in rows}
    balances = {r['starting_balance'] for r in rows}
    if len(horizons) != 1 or len(balances) != 1:
        raise SystemExit(f'batch mixes horizons {horizons} or starting balances {balances}')
    for name in ('instruction.md', 'tools.json'):
        digests = {hashlib.sha256((Path(r['result_path']).parent / name).read_bytes()).hexdigest()
                   for r in rows}
        if len(digests) != 1:
            raise SystemExit(f'trials in {batch} do not share one {name}')
    seen = {}
    for r in rows:                       # one row per model, latest attempt, never best-of
        key = r['model_requested']
        if key not in seen or r['timestamp_utc'] > seen[key]['timestamp_utc']:
            seen[key] = r
    completed = sorted([r for r in seen.values() if r['completed']], key=lambda r: -r['reward'])
    unfinished = sorted([r for r in seen.values() if not r['completed']],
                        key=lambda r: r['display_name'])
    return rows, completed, unfinished, horizons.pop(), balances.pop()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--batch', type=Path, default=DEFAULT_BATCH)
    ap.add_argument('--output', type=Path, default=None)
    args = ap.parse_args()
    batch = args.batch.resolve()
    out = (args.output or batch).resolve()
    out.mkdir(parents=True, exist_ok=True)

    rows, completed, unfinished, days, starting = load(batch)
    selected = completed + unfinished
    known = sum(r['reconciled_known_cost_usd'] for r in rows)
    reserved = sum(r.get('unresolved_cost_reservation_usd', 0) for r in rows)
    for r in rows:
        r['result_path'] = os.path.relpath(r['result_path'], out)

    summary = {'horizon_days': days, 'starting_balance': starting,
               'selection_policy': 'Latest attempt per model, not best-of or pooled mean',
               'models': len(selected), 'trials': len(rows),
               'completed_models': len(completed),
               'completed_trials': sum(r['completed'] for r in rows),
               'known_cost_usd': known, 'unresolved_reservation_usd': reserved,
               'leaderboard': selected, 'all_trials': rows}
    (out / 'aggregate.json').write_text(json.dumps(summary, indent=2))

    for r in rows:
        r['profit'] = round(profit(r), 2)
        net = after_llm_cost(r)
        r['profit_after_llm_cost'] = None if net is None else round(net, 2)

    fields = ['display_name', 'model_requested', 'completed', 'days_operated', 'reward',
              'final_balance', 'profit', 'profit_after_llm_cost',
              'reconciled_known_cost_usd', 'runtime_seconds',
              'model_result_subtype', 'timestamp_utc', 'result_path']
    for filename, data in (('leaderboard.csv', selected), ('all-trials.csv', rows)):
        with (out / filename).open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(data)

    scripted = ROOT / 'results/baselines/scripted-reference/results.json'
    reference = None
    if scripted.exists():
        ref = [r['reward'] for r in json.loads(scripted.read_text()) if r['sim_days'] == days]
        if ref:
            reference = sum(ref) / len(ref)
    lines = [f'# Model baseline — {days}-day challenge', '',
             f'**{len(selected)} models · {len(completed)} completed.** One batch, one set of run '
             f'conditions: {days} simulated days, simulation seed 1, six machines and '
             f'{money(starting)} starting cash. Every trial received the same briefing and the '
             'same 22 MCP business tools, verified by hash.', '',
             '![Model baseline](leaderboard.png)', '',
             '## What this is', '',
             'A reference point for anyone running the benchmark, not a definitive model ranking. '
             'One simulation seed and live model sampling do not establish significance. Agents '
             'ran through an isolated in-process MCP client with no access to simulator internals; '
             'reasoning effort is low where supported and other generation settings use provider '
             'defaults. Scores from a different harness, horizon or seed are not comparable with '
             'these.', '',
             'Where a model was attempted more than once, the table shows the **latest** attempt, '
             'never the best. Every attempt is kept in `all-trials.csv` and `aggregate.json`.', '',
             '| Rank | Model | Completed | Day | Score | Profit | Profit after LLM cost | API cost | Outcome |',
             '|---:|---|---|---:|---:|---:|---:|---:|---|']
    for i, r in enumerate(selected):
        cost = price(r['reconciled_known_cost_usd'])
        if r.get('unresolved_cost_reservation_usd', 0):
            cost += ' + unresolved'
        net = r['profit_after_llm_cost']
        lines.append(f"| {i + 1 if r['completed'] else '—'} | "
                     f"[{r['display_name']}]({r['result_path']}) | {r['completed']} | "
                     f"{r['days_operated']} | {money(r['reward'])} | {money(r['profit'])} | "
                     f"{'—' if net is None else money(net)} | "
                     f"{cost} | {OUTCOMES.get(r['model_result_subtype'], r['model_result_subtype'])} |")
    lines += ['',
              f'Known API charges across all {len(rows)} trials: **${known:.2f}**'
              + (f', with **${reserved:.2f}** reserved for requests whose billing never resolved. '
                 'A reservation is not a confirmed charge.' if reserved else '.'), '',
              'Unfinished trials score zero regardless of their cash balance. A time limit or API '
              'failure is not a business outcome.', '',
              'Rank and score are the business alone: nothing in the simulation charges an agent '
              'for thinking, and survival means the shop turns a profit. The profit-after-LLM-cost '
              'column restates the same profit net of what that trial actually cost to run, at the '
              'euro/dollar rate each trial recorded. It is reported for interest, not ranked.', '']
    if reference is not None:
        lines += ['## Scripted reference', '',
                  'The scripted operator in [`../scripted-reference/`](../scripted-reference/README.md) '
                  f'averages **{money(reference)}** over five seeds at this horizon. It reads catalogue '
                  'economics straight from the simulator, so it is a calibration floor for a competent '
                  'operator, not a model result.', '']
    lines += ['## Files', '',
              '- `leaderboard.png` / `leaderboard.pdf` — the chart above.',
              '- `leaderboard.csv` — one selected row per model.',
              '- `all-trials.csv` / `aggregate.json` — every trial, including failures and reconciled costs.',
              '- `manifest.json`, `models.json`, `verification.json` — batch settings and resolved model catalogue.',
              '- `evaluation-source.zip` / `.sha256` — the exact benchmark source these runs used.',
              '- one directory per trial — briefing, tool schemas, config, conversation, responses, '
              'billed usage and the final simulator trajectory.', '']
    (out / 'README.md').write_text('\n'.join(lines))

    draw(out, completed, unfinished, days, starting, reference)
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ('leaderboard', 'all_trials')}, indent=2))


def draw(out, completed, unfinished, days, starting, reference=None):
    regular = Path('/System/Library/Fonts/Supplemental/Arial.ttf')
    bold = Path('/System/Library/Fonts/Supplemental/Arial Bold.ttf')
    for p in (regular, bold):
        if p.exists():
            font_manager.fontManager.addfont(str(p))
    plt.rcParams.update({'font.family': 'Arial' if regular.exists() else 'DejaVu Sans',
                         'pdf.fonttype': 42, 'axes.unicode_minus': False})
    bg, ink, muted, grid = '#F7F8F4', '#193630', '#5D6B65', '#DCE3DC'
    bar = '#246858'
    step = 1500
    peak = max([r['final_balance'] for r in completed] + [starting, reference or 0])
    top = (int(peak // step) + 1) * step
    n = max(len(completed), 1)

    fig = plt.figure(figsize=(12.8, 12.8), dpi=150, facecolor=bg)

    def txt(x, y, s, size=14, color=ink, weight='normal', **kw):
        return fig.text(x, y, s, fontsize=size, color=color, weight=weight, **kw)

    txt(.055, .952, 'PROSUS  /  VENDING BENCH', 12, muted, 'bold')
    txt(.945, .952, f'{days}-DAY CHALLENGE', 12, muted, ha='right')
    txt(.055, .906, 'Model baseline', 31, ink, 'bold')
    txt(.055, .87, f'{days} simulated days   /   6 machines   /   '
        f'{money(starting).replace(".00", "")} starting cash', 16, muted)
    txt(.055, .821, f'{len(completed)} COMPLETED  /  {len(completed) + len(unfinished)} MODELS',
        13, ink, 'bold')
    txt(.945, .821, 'Latest attempt per model • not best-of', 13, muted, ha='right')
    txt(.09, .773, 'MODEL', 11, muted, 'bold')
    txt(.32, .773, 'FINAL BANK BALANCE', 11, muted, 'bold')
    txt(.845, .773, 'BALANCE', 11, muted, 'bold', ha='right')
    txt(.945, .773, 'API COST¹', 11, muted, 'bold', ha='right')

    bottom, height = .324, .42
    ax = fig.add_axes([.32, bottom, .405, height], facecolor=bg)
    ax.set_xlim(0, top)
    ax.set_ylim(-.5, n - .5)
    ax.invert_yaxis()
    ax.set_yticks([])
    ticks = [v for v in range(0, int(top) + 1, step)]
    ax.set_xticks(ticks)
    ax.set_xticklabels([money(t).replace('.00', '') for t in ticks], fontsize=11, color=muted)
    ax.tick_params(axis='x', length=0, pad=10)
    for spine in ax.spines.values():
        spine.set_visible(False)
    for value in ticks:
        ax.axvline(value, color=grid, lw=.8, zorder=0)
    ax.axvline(starting, color='#82918A', ls=(0, (4, 4)), lw=1, zorder=1)
    if reference:
        ax.axvline(reference, color='#C2703D', ls=(0, (1, 3)), lw=1.6, zorder=3)
    for i, r in enumerate(completed):
        y = bottom + height * (1 - (i + .5) / n)
        ax.barh(i, r['final_balance'], height=.53, color=bar, zorder=2)
        txt(.055, y, f'{i + 1:02}', 13, muted, va='center')
        txt(.09, y, r['display_name'], 16, ink, 'bold', va='center')
        txt(.845, y, money(r['final_balance']), 17, ink, 'bold', ha='right', va='center')
        txt(.945, y, price(r['reconciled_known_cost_usd']), 15, ink, ha='right', va='center')
    legend = 'Dashed line = starting cash'
    if reference:
        legend += f'    ·    Dotted line = scripted reference, {money(reference)}'
    txt(.055, .288, legend, 11, muted)

    y = .24
    if unfinished:
        rows_needed = (len(unfinished) + 1) // 2
        box_height = .042 + .022 * rows_needed
        fig.patches.append(FancyBboxPatch(
            (.055, y - box_height + .018), .89, box_height,
            boxstyle='round,pad=0.012,rounding_size=0.008',
            transform=fig.transFigure, facecolor='#E9EDE7', edgecolor='none', zorder=-1))
        txt(.068, y, 'UNFINISHED  /  OFFICIAL SCORE €0', 11, ink, 'bold')
        for i, r in enumerate(unfinished):
            label = OUTCOMES.get(r['model_result_subtype'], r['model_result_subtype']).lower()
            txt(.068 + .457 * (i % 2), y - .027 - .022 * (i // 2),
                f"{r['display_name']}  ·  {label}, day {r['days_operated']}", 11, muted)
        y -= box_height + .012

    txt(.055, y - .012, 'Exploratory baseline • one seed • live model sampling varies', 12, ink, 'bold')
    txt(.055, y - .039, 'Same briefing, same 22 MCP business tools, same simulator config for every trial.',
        11, muted)
    txt(.055, y - .060, '¹ USD, reported charges for the displayed attempt, including upstream provider '
        'charges on BYOK routes.', 10.5, muted)
    # Crop the unused canvas below the last footnote so the card has no dead band.
    width, height_in = fig.get_size_inches()
    crop = Bbox([[0, max(0.0, y - .085) * height_in], [width, height_in]])
    fig.savefig(out / 'leaderboard.png', dpi=150, facecolor=bg, bbox_inches=crop)
    fig.savefig(out / 'leaderboard.pdf', facecolor=bg, bbox_inches=crop)
    plt.close(fig)


if __name__ == '__main__':
    main()
