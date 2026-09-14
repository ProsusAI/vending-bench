"""Inspect the configuration the simulation would actually run with.

    python3 -m vending                       # validate and summarise
    python3 -m vending --dump                # the fully resolved config as JSON
    python3 -m vending --config variant.toml # check a variant before running it

Overrides from the environment ([env_overrides] and VENDING_SET) are applied
first, so what this prints is exactly what the server would use.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import config
from .catalog import PRODUCTS, SUPPLIERS


def summarise() -> str:
    sym = config.CURRENCY_SYMBOL
    lines = [
        f"config:           {config.CONFIG_PATH}",
        f"horizon:          {config.SIM_DAYS} days from {config.START_DATE}",
        f"starting capital: {sym}{config.STARTING_BALANCE:,.2f}",
        f"spot fee:         {sym}{config.DAILY_SPOT_FEE:,.2f}/day, "
        f"terminated after {config.MAX_UNPAID_DAYS} unpaid days",
        f"machine:          {config.slot_count()} slots "
        f"({'/'.join(config.MACHINE_ROWS)} x {config.SLOTS_PER_ROW}), "
        f"{config.SLOT_CAPACITY_SMALL} small / {config.SLOT_CAPACITY_LARGE} large",
        f"working day:      {config.DAY_START_MINUTE // 60:02d}:00 to "
        f"{config.DAY_END_MINUTE // 60:02d}:00 "
        f"({(config.DAY_END_MINUTE - config.DAY_START_MINUTE) // 60}h), "
        f"{len(config.TOOL_MINUTES)} priced tools",
        f"payments:         {config.CARD_SHARE:.0%} card, settling in "
        f"{config.CARD_SETTLEMENT_DAYS} day(s); cash banks overnight",
        f"catalogue:        {len(PRODUCTS)} products, {len(SUPPLIERS)} suppliers",
        "",
        "suppliers:",
    ]
    for s in SUPPLIERS.values():
        lines.append(
            f"  {s.id:<24} {s.persona:<9} markup x{s.markup:<5} "
            f"floor {s.floor:<5} {len(s.product_ids):>2} lines  "
            f"min {sym}{s.min_order_value:,.0f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m vending")
    ap.add_argument("--config", help="validate this file instead of the default")
    ap.add_argument("--dump", action="store_true",
                    help="print the resolved configuration as JSON")
    args = ap.parse_args(argv)

    try:
        if args.config:
            config.reload(args.config)
    except config.ConfigError as exc:
        print(f"invalid configuration: {exc}", file=sys.stderr)
        return 1

    if args.dump:
        print(json.dumps(config.CONFIG, indent=2, sort_keys=True))
    else:
        print(summarise())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
