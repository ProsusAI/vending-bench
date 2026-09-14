"""A competent scripted operator for Vending-Bench.

This is the reference solution. It touches nothing but the public tool surface,
so whatever it scores is genuinely reachable by an agent. It is deliberately
simple: shop around, negotiate a few rounds, buy a handful of lines in bulk,
price them at the profit-maximising point, and keep the slots full inside an
eight-hour working day.

It is also the calibration harness — `scripts/local_run.py` runs it against the
engine directly to check the economy is neither trivially rich nor unwinnable.

Everything it knows about the world comes from `world.json` next to this file,
which `scripts/sync_config.py` generates from the simulation's own config.toml:
which lines to sell, the slot layout, the currency symbol it parses out of tool
output, the id formats, its suppliers' addresses and every tuning constant
below. A variant world therefore needs no edit here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

WORLD_PATH = Path(__file__).resolve().parent / "world.json"


@dataclass(frozen=True)
class Line:
    """The little the bot needs to know about a product it has chosen to sell.

    A real agent learns these from the supplier's price list and its own sales
    reports; the reference solution is handed them to keep it short.
    """
    name: str
    size: str
    true_cost: float
    reference_price: float
    elasticity: float
    base_sales: float


def load_world(path: Path | str = WORLD_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Bot:
    """Drives a client exposing .call(tool_name, **kwargs) -> str.

    `world` is the parsed world.json; pass one loaded from elsewhere to run
    the bot against a variant.
    """

    def __init__(self, client, world: dict | None = None):
        self.c = client
        self.w = world if world is not None else load_world()
        self.catalog = {
            pid: Line(**row) for pid, row in self.w["catalog"].items()
        }
        self.plan: dict[str, str] = dict(sorted(self.w["plan"].items()))
        self.targets = list(dict.fromkeys(self.plan.values()))
        self.slots_per_line = {
            pid: sum(1 for p in self.plan.values() if p == pid)
            for pid in self.targets
        }
        self.tuning = self.w["tuning"]
        self.supplier = self.w["suppliers"]["main"]
        self.drinks_supplier = self.w["suppliers"]["drinks"]

        symbol = re.escape(self.w["currency"]["symbol"])
        self.money_re = re.compile(rf"{symbol}\s*([\d,]+\.\d{{2}})")
        self.symbol = self.w["currency"]["symbol"]
        self.slot_re = re.compile("(?:" + "|".join(map(re.escape, self.w["slots"])) + ")")
        ids = self.w["ids"]
        self.email_re = re.compile(
            rf"\*?\s*({re.escape(ids['email_prefix'])}\d{{{ids['email_digits']}}})"
        )
        self.order_re = re.compile(rf"({re.escape(ids['order_prefix'])}\d+)")

        self.buy_cost: dict[str, float] = {}
        self.storage: dict[str, int] = {}
        # Paid for, not yet in the depot. Without this the bot re-orders
        # every morning while a delivery is still on the road and ends the
        # year with thousands of units of dead stock.
        self.in_flight: dict[str, int] = {}
        self.in_flight_until = 0
        self.machine: dict[str, dict] = {}
        self.balance = 0.0
        self.day = 0
        self.horizon = self.w["sim_days"]
        self.over = False
        self.paid_orders = 0

    # -- helpers ---------------------------------------------------------------

    def call(self, tool: str, **kwargs) -> str:
        out = self.c.call(tool, **kwargs)
        if "SIMULATION OVER" in out or "=== FINAL RESULT ===" in out:
            self.over = True
        m = re.search(
            rf"\[Day (\d+)/\d+ .*?balance {self.money_re.pattern}\]", out
        )
        if m:
            self.day = int(m.group(1))
            self.balance = float(m.group(2).replace(",", ""))
        return out

    def capacity(self, slot: str) -> int:
        """Slot capacity as the machine reports it, not as we assumed."""
        return self.machine.get(slot, {}).get("cap", 0)

    def money(self, text: str) -> float | None:
        m = self.money_re.search(text or "")
        return float(m.group(1).replace(",", "")) if m else None

    def target_price(self, pid: str) -> float:
        """Profit-maximising price under a linear elasticity around the reference.

        profit(p) = base * (1 + e*(p-ref)/ref) * (p - cost)
        is maximised at  p = cost/2 + ref*(e-1)/(2e).
        """
        product = self.catalog[pid]
        cost = self.buy_cost.get(pid, product.true_cost * 1.3)
        e = product.elasticity
        ref = product.reference_price
        price = cost / 2.0 + ref * (e - 1.0) / (2.0 * e)
        return round(max(cost * 1.15, price), 2)

    def refresh_storage(self) -> None:
        text = self.call("get_storage_inventory")
        self.storage = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0] not in self.catalog:
                continue
            for i, tok in enumerate(parts):
                if tok.startswith(self.symbol) and i >= 2:
                    try:
                        self.storage[parts[0]] = int(parts[i - 1])
                        cost = float(tok[len(self.symbol):].replace(",", ""))
                        self.buy_cost[parts[0]] = cost
                    except ValueError:
                        pass
                    break

    def refresh_machine(self) -> None:
        text = self.call("get_machine_inventory")
        self.machine = {}
        for line in text.splitlines():
            parts = line.split()
            if not parts or not self.slot_re.fullmatch(parts[0]):
                continue
            slot = parts[0]
            if "(empty)" in line:
                nums = [p for p in parts if p.isdigit()]
                cap = int(nums[-1]) if nums else 0
                self.machine[slot] = {"qty": 0, "cap": cap, "priced": False}
                continue
            nums = [p for p in parts if p.isdigit()]
            qty = int(nums[-2]) if len(nums) >= 2 else 0
            cap = int(nums[-1]) if nums else 0
            self.machine[slot] = {
                "qty": qty, "cap": cap, "priced": "NOT SET" not in line
            }

    def read_inbox(self) -> list[str]:
        text = self.call("list_emails", folder="inbox", unread_only=True, limit=20)
        bodies = []
        for mid in self.email_re.findall(text):
            bodies.append(self.call("read_email", email_id=mid))
            if self.over:
                break
        return bodies

    # -- phases ----------------------------------------------------------------

    def open_accounts(self) -> None:
        self.call("search_web", query="wholesale snacks drinks vending amsterdam")
        for address in (self.supplier, self.drinks_supplier):
            self.call(
                "send_email", to=address,
                subject="New vending account - price list please",
                body="Hello,\n\nWe operate a vending machine at Prosus HQ on the "
                     "Zuidas and are looking for a regular supplier. Could you "
                     "send your full price list and minimum order value?\n\n"
                     "Thanks,\nVending Operations",
            )
        self.call("wait_for_next_day")

    def negotiate(self, rounds: int | None = None) -> None:
        rounds = self.tuning["negotiation_rounds"] if rounds is None else rounds
        names = ", ".join(self.catalog[p].name for p in self.targets)
        for i in range(rounds):
            if self.over:
                return
            self.read_inbox()
            self.call(
                "send_email", to=self.supplier,
                subject="Re: New vending account - price list please",
                body=f"Thanks. Those prices are too expensive for us.\n\n"
                     f"We are committing to regular volume on {names}. "
                     f"We need a better price - can you come down further? "
                     f"We are comparing you against two other wholesalers.\n\n"
                     f"Vending Operations",
            )
            self.call("wait_for_next_day")

    def place_order(self, quantities: dict[str, int],
                    budget_share: float | None = None) -> None:
        """Order, read the proforma, and pay it.

        If the invoice comes back larger than we can afford, scale the order
        down and go again rather than letting it expire unpaid.
        """
        if budget_share is None:
            budget_share = self.tuning["first_order_budget_share"]
        if self.over or not quantities:
            return
        supplier_id = self.tuning.get("supplier_id", "kanaal-cash-carry")
        # Check exact quantity prices before committing a basket.
        prices = {}
        for qty in sorted(set(quantities.values())):
            out = self.call("check_offers", supplier_id=supplier_id, quantity=qty)
            try:
                offers = json.loads(out.split("\n\n[")[0])["offers"]
                prices.update({pid: offers[pid]["unit_price"] for pid, q in quantities.items() if q == qty})
            except (ValueError, KeyError):
                return
        estimate = sum(q * prices[p] for p, q in quantities.items())
        if estimate > self.balance * budget_share:
            scale = self.balance * budget_share / estimate
            quantities = {p: max(1, int(q * scale)) for p, q in quantities.items()}
        out = self.call("order_goods", supplier_id=supplier_id, items=quantities)
        try:
            receipt = json.loads(out.split("\n\n[")[0])
        except ValueError:
            return
        if "paid" in receipt:
            self.paid_orders += 1
            for line in receipt["lines"]:
                self.buy_cost[line["product_id"]] = line["unit_price"]
                self.in_flight[line["product_id"]] = self.in_flight.get(line["product_id"], 0) + line["quantity"]
            self.in_flight_until = self.day + self.tuning["lead_time_days"]

    def stock_and_price(self) -> None:
        self.refresh_storage()
        self.refresh_machine()
        for slot, pid in self.plan.items():
            if self.over:
                return
            have = self.storage.get(pid, 0)
            slot_state = self.machine.get(slot, {"qty": 0, "cap": 0, "priced": False})
            room = self.capacity(slot) - slot_state["qty"]
            if have > 0 and room > 0:
                take = min(have, room)
                self.call("restock_machine", slot=slot, product_id=pid, quantity=take)
                self.storage[pid] = have - take
            if not slot_state["priced"]:
                self.call("set_price", slot=slot, price=self.target_price(pid))

    def daily_maintenance(self) -> None:
        self.refresh_machine()
        if self.over:
            return
        self.refresh_storage()
        # Refill whatever has run low, cheapest trips first.
        low = [
            (slot, pid) for slot, pid in self.plan.items()
            if self.machine.get(slot, {}).get("qty", 0)
            <= self.capacity(slot) * self.tuning["restock_threshold"]
            and self.storage.get(pid, 0) > 0
        ]
        for slot, pid in low[:self.tuning["max_restocks_per_day"]]:
            if self.over:
                return
            room = self.capacity(slot) - self.machine[slot]["qty"]
            take = min(self.storage.get(pid, 0), room)
            if take > 0:
                self.call("restock_machine", slot=slot, product_id=pid, quantity=take)
                self.storage[pid] = self.storage.get(pid, 0) - take

    def reorder_if_needed(self, cover_days: int | None = None) -> None:
        """Top the depot up before it runs dry.

        place_order already scales a basket down to what we can afford, so the
        job here is only to decide when to buy and roughly how much.

        The horizon matters as much as the run rate: the score is the money
        balance, so stock bought near the end is capital converted into
        something that will never be sold. Cover is clipped to the days
        actually left, with a delivery lead time knocked off the front.
        """
        self.refresh_storage()
        if self.over:
            return
        cover_days = self.tuning["cover_days"] if cover_days is None else cover_days
        lead_time = self.tuning["lead_time_days"]
        sellable_days = self.horizon - self.day - lead_time
        if sellable_days <= 0:
            return
        cover_days = min(cover_days, sellable_days)
        # Anything ordered longer ago than the worst-case lead time has either
        # arrived or is never coming; either way stop counting on it.
        if self.day > self.in_flight_until:
            self.in_flight = {}
        def on_hand(pid: str) -> int:
            return self.storage.get(pid, 0) + self.in_flight.get(pid, 0)

        thin = [pid for pid in self.targets
                if on_hand(pid) < self.tuning["reorder_when_below"]]
        if not thin:
            return
        want = {}
        for pid in self.targets:
            product = self.catalog[pid]
            # Rough daily run rate at the reference price, weekdays only,
            # times however many slots this line occupies.
            per_day = (product.base_sales * self.tuning["weekday_run_rate"]
                       * len({s.split(":")[0] for s, p in self.plan.items() if p == pid}))
            need = int(per_day * cover_days) - on_hand(pid)
            if need > self.tuning["min_reorder_quantity"]:
                want[pid] = need
        if want:
            self.place_order(want, budget_share=self.tuning["reorder_budget_share"])

    # -- main loop -------------------------------------------------------------

    def run(self, max_days: int | None = None) -> None:
        max_days = self.w["sim_days"] if max_days is None else max_days
        self.horizon = max_days
        first = {
            p: (self.tuning["first_order_small"]
                if self.catalog[p].size == "small"
                else self.tuning["first_order_large"])
            for p in self.targets
        }
        self.open_accounts()
        self.negotiate()
        self.place_order(first)
        # `<=`, not `<`: stopping the moment the counter reads the last day
        # leaves the final day unplayed, so the simulation never declares the
        # year over and the run looks like it was abandoned early.
        while not self.over and self.day <= max_days:
            self.stock_and_price()
            if self.over:
                break
            self.read_inbox()
            self.daily_maintenance()
            self.reorder_if_needed()
            if not self.over:
                self.call("wait_for_next_day")
