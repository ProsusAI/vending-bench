"""Run the reference bot against the engine in-process.

    python3 scripts/local_run.py --seed 1
    python3 scripts/local_run.py --seed 1 --days 365
    python3 scripts/local_run.py --config .../variants/full-year.toml

The horizon defaults to whatever config is loaded, which is 30 days unless a
variant or VENDING_SIM_DAYS says otherwise.

No Docker, no MCP, no model. Used to check the economy is balanced and that a
run completes without the engine throwing.

The bot's world is derived from whatever config is loaded rather than read from
solution/world.json, so calibrating a variant does not need the generated file
to be regenerated first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tasks" / "vending-bench" / "environment" / "sim-server"))
# The reference bot lives with the solution that ships in the task image; there
# is deliberately only one copy of it, so a fix cannot land in just one place.
sys.path.insert(0, str(ROOT / "tasks" / "vending-bench" / "solution"))

sys.path.insert(0, str(ROOT / "scripts"))

from vending import config  # noqa: E402
from vending.engine import Engine  # noqa: E402
from reference_bot import Bot  # noqa: E402
from sync_config import world as build_world  # noqa: E402


class DirectClient:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.calls = 0

    def call(self, tool: str, **kwargs) -> str:
        self.calls += 1
        return getattr(self.engine, f"tool_{tool}")(**kwargs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--days", type=int, default=None,
                    help="defaults to the loaded config's horizon")
    ap.add_argument("--config", help="calibrate a variant config file")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.config:
        config.reload(args.config)
    days = config.SIM_DAYS if args.days is None else args.days

    engine = Engine.new(args.seed)
    bot = Bot(DirectClient(engine), build_world())
    bot.run(max_days=days)

    s = engine.s
    sym = config.CURRENCY_SYMBOL
    if not args.quiet:
        print(f"seed={args.seed}")
        print(f"  days operated      {s['day']}")
        print(f"  tool calls         {s['tool_calls']}")
        print(f"  units sold         {s['units_sold']}")
        print(f"  gross revenue      {sym}{s['gross_revenue']:,.2f}")
        print(f"  spot fees          {sym}{s['fees_paid']:,.2f}")
        print(f"  reputation         {s['reputation']:.2f}")
        print(f"  FINAL BALANCE      {sym}{s['balance']:,.2f}")
        print(f"  net worth          {sym}{engine.net_worth():,.2f}")
        print(f"  PROFIT             {sym}{engine.net_worth() - config.STARTING_BALANCE:,.2f}")
        if s["termination_reason"]:
            print(f"  ended: {s['termination_reason']}")
    else:
        print(f"{args.seed}\t{s['day']}\t{s['balance']:.2f}\t{engine.net_worth():.2f}\t{s['units_sold']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
