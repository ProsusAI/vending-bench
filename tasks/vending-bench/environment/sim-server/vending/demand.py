"""Daily customer purchase simulation.

Implements the five-step purchase model established in the published
vending-benchmark literature (see NOTICE, "Prior art"):

  1. Each item carries a price elasticity, a reference price and a base
     sales rate (declared in config.toml rather than LLM-generated).
  2. A sales impact factor is derived from the percentage difference between
     the set price and the reference price times the elasticity, and multiplies
     base sales.
  3. Base sales are modified by day-of-week and monthly multipliers plus a
     weather impact factor.
  4. A choice multiplier rewards optimal product variety and penalises excess
     options, capped at a 50% reduction.
  5. The prediction takes random noise, rounds to whole units, and is capped
     between zero and available inventory. The rounding is probabilistic (see
     stochastic_round) so slow-moving high-value lines are rare, not impossible.

Step 3 here also carries a reputation multiplier, which is this benchmark's
hook for customer complaints.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from . import config
from .catalog import PRODUCTS


@dataclass
class SlotSale:
    slot: str
    product_id: str
    units: int
    unit_price: float

    @property
    def revenue(self) -> float:
        return round(self.units * self.unit_price, 2)


def price_impact(price: float, reference_price: float, elasticity: float) -> float:
    """Step 2: sales impact factor from the percentage difference vs reference."""
    if reference_price <= 0:
        return 0.0
    pct_diff = (price - reference_price) / reference_price
    impact = 1.0 + elasticity * pct_diff
    return max(0.0, min(config.MAX_PRICE_IMPACT, impact))


def choice_multiplier(distinct_products: int) -> float:
    """Step 4: variety helps up to a point, then splits the same footfall.

    Capped at MAX_CHOICE_PENALTY in either direction. The optimum, the cap
    and both slopes come from [demand] in config.toml.
    """
    if distinct_products <= 0:
        return 0.0
    optimal = config.OPTIMAL_DISTINCT_PRODUCTS
    floor = 1.0 - config.MAX_CHOICE_PENALTY
    if distinct_products == optimal:
        return 1.0
    if distinct_products < optimal:
        # Missing variety: linear ramp from the cap up to 1.0 at optimal.
        shortfall = (optimal - distinct_products) / optimal
        return max(floor, 1.0 - shortfall * config.CHOICE_SHORTFALL_SLOPE)
    # Too much choice: gentler decay, still capped.
    excess = distinct_products - optimal
    return max(floor, 1.0 - excess * config.CHOICE_EXCESS_SLOPE)


def stochastic_round(x: float, rng: random.Random) -> int:
    """Round to a whole number of units without destroying slow sellers.

    Plain round() floors anything under half a unit a day to zero forever, which
    silently makes every low-volume line unsellable at any price: a product
    expected to move one unit a week never moves one at all. Rounding the
    fractional part probabilistically keeps the expected value exact, so an item
    at 0.12 units/day sells roughly once every eight days instead of never.
    """
    if x <= 0:
        return 0
    whole = int(x)
    return whole + (1 if rng.random() < (x - whole) else 0)


def day_multiplier(date, weather: str, reputation: float) -> tuple[float, dict]:
    """Step 3: everything that scales the whole machine on a given day."""
    dow = config.DAY_OF_WEEK_MULTIPLIER[date.weekday()]
    month = config.MONTH_MULTIPLIER[date.month]
    holiday = config.DUTCH_HOLIDAYS.get((date.month, date.day), 1.0)
    rep = max(config.REPUTATION_FLOOR, min(config.REPUTATION_CEILING, reputation))
    breakdown = {
        "day_of_week": dow,
        "month": month,
        "holiday": holiday,
        "reputation": rep,
        "weather": weather,
    }
    return dow * month * holiday * rep, breakdown


def simulate_day(
    machine: dict,
    date,
    weather: str,
    reputation: float,
    rng: random.Random,
    traffic: dict | None = None,
) -> tuple[list[SlotSale], dict]:
    """Run one day of sales across the machine.

    `machine` maps slot -> {"product_id": str, "quantity": int, "price": float}.
    Returns the per-slot sales and a breakdown of the multipliers used, which
    the agent can read back through get_sales_report.
    """
    groups: dict[str, dict] = {}
    for slot, state in machine.items():
        if state.get("product_id") and state.get("quantity", 0) > 0 and state.get("price") is not None:
            mid = slot.rsplit(":", 1)[0] if ":" in slot else "legacy"
            groups.setdefault(mid, {})[slot] = state
    global_mult, breakdown = day_multiplier(date, weather, reputation)
    weather_table = config.WEATHER_CATEGORY_MULTIPLIER[weather]
    sales = []
    breakdown["machines"] = {}
    for mid, stocked in sorted(groups.items()):
        location = mid.split(":")[0]
        curve = config.LOCATIONS.get(location, {"volume": 1., "price_sensitivity": 1., "preferences": {}})
        distinct = len({s["product_id"] for s in stocked.values()})
        choice = choice_multiplier(distinct)
        marketing = (traffic or {}).get(location, 1.0)
        breakdown["machines"][mid] = {"choice": choice, "volume": curve["volume"], "marketing": marketing}
        for pid in sorted({s["product_id"] for s in stocked.values()}):
            product = PRODUCTS.get(pid)
            if product is None:
                continue
            slots = sorted(((slot, s) for slot, s in stocked.items() if s["product_id"] == pid),
                           key=lambda pair: (pair[1]["price"], pair[0]))
            # Shared customer willingness: additional facings add capacity, not customers.
            noise = max(0., rng.gauss(config.DEMAND_NOISE_MEAN, config.DEMAND_NOISE_SD))
            rounding = rng.random()
            base = (product.base_sales * global_mult * choice * curve["volume"] * marketing
                    * curve["preferences"].get(product.category, 0.)
                    * weather_table.get(product.category, 1.) * noise)
            if mid == "legacy":
                base = product.base_sales * global_mult * choice * weather_table.get(product.category, 1.) * noise
            served = 0
            for slot, state in slots:
                price = float(state["price"])
                impact = price_impact(price, product.reference_price, product.elasticity * curve["price_sensitivity"])
                expected = base * impact
                willingness = int(expected) + int(rounding < expected % 1)
                units = min(state["quantity"], max(0, willingness - served))
                served += units
                if units:
                    sales.append(SlotSale(slot, pid, units, price))
    return sales, breakdown
