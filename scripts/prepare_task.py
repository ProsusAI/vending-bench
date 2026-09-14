"""Materialize a self-contained Harbor task with a matching briefing and oracle.

The shipped task runs the default 30-day challenge. Use this to freeze any
other horizon into its own task directory, briefing and reference world:

    python scripts/prepare_task.py --days 30  --output /tmp/prosus-vending-30d
    python scripts/prepare_task.py --days 365 --output /tmp/prosus-vending-year
"""
import argparse
import shutil
from pathlib import Path
import sync_config
from vending import config

FULL_YEAR = 365

# Scripted-reference tuning that only makes sense over a full year. Mirrors
# variants/full-year.toml, which stays the config-only way to get the same world.
YEAR_TUNING = (
    ('cover_days = 10', 'cover_days = 45'),
    ('first_order_small = 100', 'first_order_small = 150'),
    ('negotiation_rounds = 2', 'negotiation_rounds = 4'),
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    if args.days < 1:
        ap.error('--days must be positive')
    if args.output.exists():
        ap.error('--output must be a new directory')
    variant = sync_config.TASK / 'environment/sim-server/variants/full-year.toml'
    config.reload(variant if args.days == FULL_YEAR else None,
                  environ={'VENDING_SIM_DAYS': str(args.days)})
    shutil.copytree(sync_config.TASK, args.output,
                    ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    path = args.output / 'environment/sim-server/vending/config.toml'
    # The copied source config is the resolved 30-day base; retune it in place.
    text = path.read_text().replace('sim_days = 30', f'sim_days = {args.days}')
    if args.days == FULL_YEAR:
        for old, new in YEAR_TUNING:
            text = text.replace(old, new)
    path.write_text(text)
    (args.output / 'instruction.md').write_text(sync_config.render())
    (args.output / 'solution/world.json').write_text(sync_config.render_world())
    manifest = args.output / 'task.toml'
    manifest.write_text(manifest.read_text().replace(
        'name = "prosus/prosus-vending-bench"',
        f'name = "prosus/prosus-vending-bench-{args.days}d"'))
    print(args.output.resolve())


if __name__ == '__main__':
    main()
