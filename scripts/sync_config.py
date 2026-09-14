"""Regenerate everything outside the simulation that depends on its config.

Two files live outside the sim-server image but have to agree with it:

  instruction.md      the agent is told the starting capital, the spot fee, the
                      slot layout and the horizon. A variant config would
                      otherwise hand the agent a briefing that lies to it.
  solution/world.json what the reference operator knows about the world — the
                      slot layout, the id formats, the currency symbol it parses
                      out of tool output, its supplier addresses and the
                      economics of the lines it sells. The oracle runs in the
                      agent container and cannot import vending, so it reads this.

    python3 scripts/sync_config.py            # regenerate both
    python3 scripts/sync_config.py --check    # fail if either is stale

`--config` renders against a variant file instead of the default.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK = ROOT / "tasks" / "vending-bench"
sys.path.insert(0, str(TASK / "environment" / "sim-server"))

from vending import config  # noqa: E402
from vending.catalog import PRODUCTS, SUPPLIERS  # noqa: E402

TEMPLATE = TASK / "instruction.template.md"
INSTRUCTION = TASK / "instruction.md"
WORLD = TASK / "solution" / "world.json"


def placeholders() -> dict:
    """Everything instruction.template.md is allowed to reference."""
    def hhmm(minute: int) -> str:
        return f"{minute // 60:02d}:{minute % 60:02d}"

    return {
        "currency": config.CURRENCY,
        "symbol": config.CURRENCY_SYMBOL,
        "sim_days": config.SIM_DAYS,
        "start_date": config.START_DATE,
        "starting_balance": f"{config.STARTING_BALANCE:,.0f}",
        "daily_fee": f"{config.DAILY_SPOT_FEE:,.0f}",
        "max_unpaid_days": config.MAX_UNPAID_DAYS,
        "machine_address": config.MACHINE_ADDRESS,
        "storage_address": config.STORAGE_ADDRESS,
        "slot_count": config.slot_count(),
        "small_rows": " and ".join(config.SMALL_ROWS),
        "large_rows": " and ".join(config.LARGE_ROWS),
        "slot_capacity_small": config.SLOT_CAPACITY_SMALL,
        "slot_capacity_large": config.SLOT_CAPACITY_LARGE,
        "order_id_example": f"{config.ORDER_ID_PREFIX}{config.ORDER_ID_START}",
        "complaint_prefix": config.COMPLAINT_PREFIX,
        "day_start": hhmm(config.DAY_START_MINUTE),
        "day_end": hhmm(config.DAY_END_MINUTE),
        "day_hours": (config.DAY_END_MINUTE - config.DAY_START_MINUTE) // 60,
        "restock_minutes": config.TOOL_MINUTES["restock_machine"],
        "swap_minutes": config.TOOL_MINUTES["swap_item"],
    }


def render() -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    # Strip the "this file is a template" banner from the rendered briefing.
    if template.startswith("<!--"):
        template = template[template.index("-->") + 4:].lstrip("\n")
    return template.format(**placeholders())


def plan() -> dict[str, str]:
    """Share each size class's slots out evenly across its configured lines."""
    assignment = {}
    snacks = ["stroopwafel-2pk", "choco-bar-100g", "nuts-mix-75g", "protein-bar-60g"]
    large_snacks = ["crisps-paprika-335g", "tortilla-chips-300g"]
    drinks = ["cola-500ml", "energy-drink-500ml", "sparkling-water-500ml", "oat-latte-can"]
    for mid in config.machine_ids():
        for i, slot in enumerate(s for s in config.all_slots() if s.startswith(mid + ":")):
            lines = drinks if ":DRINKS" in mid else (snacks if config.slot_size_class(slot) == "small" else large_snacks)
            # Only stocked supplier lines; substitute protein with gummy bears.
            lines = ["gummy-bears-150g" if p == "protein-bar-60g" else p for p in lines]
            assignment[slot] = lines[i % len(lines)]
    return assignment


def world() -> dict:
    """The snapshot solution/reference_bot.py reads instead of importing vending."""
    sol = config.SOLUTION
    assignment = plan()
    used = sorted(set(assignment.values()))
    return {
        "_generated_by": "scripts/sync_config.py — do not edit by hand",
        "currency": {"code": config.CURRENCY, "symbol": config.CURRENCY_SYMBOL},
        "sim_days": config.SIM_DAYS,
        "locations": config.LOCATIONS,
        "slots": {
            slot: {
                "size": config.slot_size_class(slot),
                "capacity": config.slot_capacity(slot),
            }
            for slot in config.all_slots()
        },
        "ids": {
            "order_prefix": config.ORDER_ID_PREFIX,
            "email_prefix": config.EMAIL_ID_PREFIX,
            "email_digits": config.EMAIL_ID_DIGITS,
            "complaint_prefix": config.COMPLAINT_PREFIX,
        },
        "suppliers": {
            "main": SUPPLIERS[sol["main_supplier"]].email,
            "drinks": SUPPLIERS[sol["drinks_supplier"]].email,
        },
        "plan": assignment,
        "tuning": {k: v for k, v in sol.items()
                   if k not in ("small_lines", "large_lines",
                                "main_supplier", "drinks_supplier")},
        "catalog": {
            pid: {
                "name": PRODUCTS[pid].name,
                "size": PRODUCTS[pid].size,
                "true_cost": PRODUCTS[pid].true_cost,
                "reference_price": PRODUCTS[pid].reference_price,
                "elasticity": PRODUCTS[pid].elasticity,
                "base_sales": PRODUCTS[pid].base_sales,
            }
            for pid in used
        },
    }


def render_world() -> str:
    return json.dumps(world(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if either generated file is stale")
    ap.add_argument("--config", help="render against a variant config file")
    args = ap.parse_args()

    if args.config:
        config.reload(args.config)

    outputs = [(INSTRUCTION, render()), (WORLD, render_world())]
    if args.check:
        stale = [
            path for path, wanted in outputs
            if not path.exists()
            or path.read_text(encoding="utf-8") != wanted
        ]
        if stale:
            for path in stale:
                print(f"{path.relative_to(ROOT)} is out of date", file=sys.stderr)
            print("Run: python3 scripts/sync_config.py", file=sys.stderr)
            return 1
        print("instruction.md and solution/world.json are up to date.")
        return 0

    for path, text in outputs:
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)} from {config.CONFIG_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
