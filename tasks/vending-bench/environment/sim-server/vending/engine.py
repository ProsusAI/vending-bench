"""The Vending-Bench world.

Holds all simulation state, advances the clock as the agent uses tools, and
runs end-of-day processing: sales, the spot fee, card settlement, deliveries,
overnight supplier replies, customer complaints and supplier collapses.

State is a plain dict so it can be written to JSON after every tool call. The
verifier reads that file to score the run, and a crashed container can be
resumed from it.

Every constant this module uses comes from `config`, which reads config.toml.
There are no economic numbers spelled out below.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date as Date, datetime, timedelta
from pathlib import Path

from . import config, demand, mailroom
from .catalog import PRODUCTS, SUPPLIERS, Supplier, supplier_for_email


def _round(x: float) -> float:
    return round(x + 1e-9, 2)


class Terminated(Exception):
    """Raised internally when the run ends mid-tool-call."""


class Engine:
    def __init__(self, state: dict, path: Path | None = None):
        self.s = state
        self.path = path
        self._reseed()

    # --- lifecycle -----------------------------------------------------------

    @classmethod
    def new(cls, seed: int, path: Path | None = None) -> "Engine":
        start = datetime.strptime(config.START_DATE, "%Y-%m-%d").date()
        state = {
            "schema_version": 3,
            "seed": seed,
            "day": 1,
            "minute": config.DAY_START_MINUTE,
            "start_date": start.isoformat(),
            "balance": config.STARTING_BALANCE,
            "marketing": [],
            "marketing_spend": 0.0,
            "sealed": False,
            "pending_card": [],
            "machine": {slot: {"product_id": None, "quantity": 0, "price": None}
                        for slot in config.all_slots()},
            "storage": {},
            "emails": [],
            "orders": {},
            "negotiations": {},
            "notes": {},
            "reminders": [],
            "sales_log": [],
            "weather": config.WEATHER_FALLBACK,
            "reputation": config.REPUTATION_START,
            "complaints": {},
            "unpaid_days": 0,
            "tool_calls": 0,
            "fees_paid": 0.0,
            "defunct_suppliers": [],
            "terminated": False,
            "termination_reason": None,
            "finished": False,
            "units_sold": 0,
            "gross_revenue": 0.0,
            "cash_banked": 0.0,
            "events": [],
        }
        engine = cls(state, path)
        engine.s["weather"] = engine._roll_weather(engine.date)
        engine._seed_inbox()
        engine.save()
        return engine

    @classmethod
    def load(cls, path: Path) -> "Engine":
        state = json.loads(path.read_text())
        if state.get("schema_version") != 3 or set(state["machine"]) != set(config.all_slots()):
            raise ValueError("State belongs to another benchmark version or machine layout; start a fresh run.")
        return cls(state, path)

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.s, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def _reseed(self) -> None:
        """Derive the day's randomness from the seed plus the day number.

        Keeps a run reproducible even though the agent's tool calls arrive in
        an unpredictable order.
        """
        self.rng = random.Random(f"{self.s['seed']}:{self.s['day']}:{len(self.s['events'])}")

    # --- clock ---------------------------------------------------------------

    @property
    def date(self) -> Date:
        return Date.fromisoformat(self.s["start_date"]) + timedelta(days=self.s["day"] - 1)

    @property
    def clock(self) -> str:
        m = self.s["minute"]
        return f"{m // 60:02d}:{m % 60:02d}"

    @property
    def over(self) -> bool:
        return bool(self.s["finished"] or self.s["terminated"] or self.s.get("sealed", False))

    def status_line(self) -> str:
        return (
            f"Day {self.s['day']}/{config.SIM_DAYS} — {self.date.strftime('%a %d %b %Y')} "
            f"{self.clock} — balance {config.CURRENCY_SYMBOL}{self.s['balance']:,.2f}"
        )

    def spend_time(self, tool: str) -> list[str]:
        """Advance the clock for a tool call. Returns any overnight notices."""
        self.s["tool_calls"] += 1
        minutes = config.TOOL_MINUTES.get(tool, config.DEFAULT_TOOL_MINUTES)
        self.s["minute"] += minutes
        notices: list[str] = []
        while self.s["minute"] >= config.DAY_END_MINUTE and not self.over:
            notices.extend(self._roll_day())
        return notices

    def wait_for_next_day(self) -> list[str]:
        self.s["tool_calls"] += 1
        if self.over:
            return []
        return self._roll_day()

    def _roll_day(self) -> list[str]:
        notices = self.end_of_day()
        self.s["day"] += 1
        self.s["minute"] = config.DAY_START_MINUTE
        self._reseed()
        if self.s["day"] > config.SIM_DAYS:
            self.s["finished"] = True
            self.s["day"] = config.SIM_DAYS
            notices.extend(self._settle_outstanding_card())
            notices.append(
                f"Day {config.SIM_DAYS} is over. The simulation has ended."
            )
            return notices
        self.s["weather"] = self._roll_weather(self.date)
        notices.extend(self._morning_briefing())
        return notices

    def _roll_weather(self, day: Date) -> str:
        table = config.WEATHER_BY_MONTH[day.month]
        r = self.rng.random()
        cumulative = 0.0
        for kind, p in table.items():
            cumulative += p
            if r <= cumulative:
                return kind
        return config.WEATHER_FALLBACK

    # --- end of day ----------------------------------------------------------

    def end_of_day(self) -> list[str]:
        notices: list[str] = []
        self._reseed()
        day = self.s["day"]

        # 1. Sales.
        sales, breakdown = demand.simulate_day(
            self.s["machine"], self.date, self.s["weather"],
            self.s["reputation"], random.Random(f"sales:{self.s['seed']}:{day}"),
            {loc: self.marketing_traffic(loc) for loc in config.LOCATIONS},
        )
        units = 0
        revenue = 0.0
        card = 0.0
        cash = 0.0
        per_slot = []
        payment_rng = random.Random(f"payments:{self.s['seed']}:{day}")
        for sale in sales:
            slot = self.s["machine"][sale.slot]
            mid = sale.slot.rsplit(":", 1)[0]
            for _ in range(sale.units):
                if payment_rng.random() < config.CARD_SHARE:
                    card += sale.unit_price
                else:
                    cash += sale.unit_price
            slot["quantity"] -= sale.units
            units += sale.units
            amount = _round(sale.units * sale.unit_price)
            revenue += amount
            if sale.units:
                per_slot.append({"slot": sale.slot, "machine": mid,
                    "location": mid.split(":")[0], "product_id": sale.product_id,
                    "units": sale.units, "unit_price": sale.unit_price, "revenue": amount})
        revenue = _round(revenue)
        self.s["units_sold"] += units
        self.s["gross_revenue"] = _round(self.s["gross_revenue"] + revenue)
        # Coin and note takings are banked overnight by the cash-in-transit
        # contract, so they land the same night. Card takings settle later.
        if cash > 0:
            self.s["balance"] = _round(self.s["balance"] + _round(cash))
            self.s["cash_banked"] = _round(self.s.get("cash_banked", 0.0) + _round(cash))
        if card > 0:
            self.s["pending_card"].append({"day": day + config.CARD_SETTLEMENT_DAYS, "amount": _round(card)})

        self.s["sales_log"].append({
            "day": day, "date": self.date.isoformat(), "weather": self.s["weather"],
            "units": units, "revenue": revenue, "per_slot": per_slot,
            "breakdown": breakdown,
        })
        # Keep the log bounded; the agent reads recent history, and the
        # verifier only needs the totals.
        cap = config.SALES_LOG_MAX_DAYS
        if len(self.s["sales_log"]) > cap:
            self.s["sales_log"] = self.s["sales_log"][-cap:]

        # 2. Card settlements landing. Today's takings were appended above with
        # a settlement day of `day + CARD_SETTLEMENT_DAYS`, so the comparison
        # has to be against `day` — comparing against `day + 1` paid them out
        # the same night and made the settlement delay a no-op.
        still_pending = []
        for entry in self.s["pending_card"]:
            if entry["day"] <= day:
                self.s["balance"] = _round(self.s["balance"] + entry["amount"])
            else:
                still_pending.append(entry)
        self.s["pending_card"] = still_pending

        # 3. The spot fee.
        if self.s["balance"] >= config.DAILY_SPOT_FEE:
            self.s["balance"] = _round(self.s["balance"] - config.DAILY_SPOT_FEE)
            self.s["fees_paid"] = _round(self.s["fees_paid"] + config.DAILY_SPOT_FEE)
            self.s["unpaid_days"] = 0
        else:
            self.s["unpaid_days"] += 1
            notices.append(
                f"WARNING: could not pay the {config.CURRENCY_SYMBOL}"
                f"{config.DAILY_SPOT_FEE:.2f} daily spot fee. "
                f"{self.s['unpaid_days']} consecutive day(s) unpaid; the contract is "
                f"cancelled after {config.MAX_UNPAID_DAYS}."
            )
            if self.s["unpaid_days"] > config.MAX_UNPAID_DAYS:
                self.s["terminated"] = True
                self.s["termination_reason"] = (
                    f"Unable to pay the daily spot fee for more than "
                    f"{config.MAX_UNPAID_DAYS} consecutive days."
                )
                notices.append(
                    "TERMINATED: the building has removed the machine for non-payment."
                )
                return notices

        # 4. Deliveries, supplier attrition, mail, complaints, spoilage.
        notices.extend(self._process_deliveries(day))
        notices.extend(self._supplier_attrition())
        notices.extend(self._overnight_mail())
        notices.extend(self._customer_events(units))
        notices.extend(self._spoilage())
        self._expire_orders(day)
        self._update_reputation()
        return notices

    def _morning_briefing(self) -> list[str]:
        unread = sum(1 for e in self.s["emails"] if e["folder"] == "inbox" and not e["read"])
        due = [r for r in self.s["reminders"]
               if not r["done"] and r["day"] <= self.s["day"]]
        out = [
            f"--- Day {self.s['day']}, {self.date.strftime('%A %d %B %Y')}. "
            f"Weather: {self.s['weather']}. "
            f"Balance {config.CURRENCY_SYMBOL}{self.s['balance']:,.2f}. "
            f"{unread} unread email(s)."
        ]
        yesterday = self.s["sales_log"][-1] if self.s["sales_log"] else None
        if yesterday:
            out.append(
                f"Yesterday sold {yesterday['units']} units for "
                f"{config.CURRENCY_SYMBOL}{yesterday['revenue']:,.2f}."
            )
        for r in due:
            out.append(f"REMINDER: {r['text']}")
            r["done"] = True
        return out

    # --- storage -------------------------------------------------------------

    def _add_stock(self, product_id: str, quantity: int, unit_cost: float) -> None:
        entry = self.s["storage"].setdefault(
            product_id, {"quantity": 0, "batches": []}
        )
        entry["quantity"] += quantity
        product = PRODUCTS[product_id]
        expiry = (
            self.s["day"] + product.shelf_life_days
            if product.shelf_life_days else None
        )
        entry["batches"].append({
            "quantity": quantity, "unit_cost": _round(unit_cost),
            "received_day": self.s["day"], "expiry_day": expiry,
        })

    def _take_stock(self, product_id: str, quantity: int) -> tuple[int, float]:
        """Remove up to `quantity` units, oldest batch first. Returns (taken, cost)."""
        entry = self.s["storage"].get(product_id)
        if not entry:
            return 0, 0.0
        remaining = quantity
        cost = 0.0
        for batch in list(entry["batches"]):
            if remaining <= 0:
                break
            take = min(batch["quantity"], remaining)
            batch["quantity"] -= take
            remaining -= take
            cost += take * batch["unit_cost"]
            if batch["quantity"] <= 0:
                entry["batches"].remove(batch)
        taken = quantity - remaining
        entry["quantity"] -= taken
        if entry["quantity"] <= 0 and not entry["batches"]:
            self.s["storage"].pop(product_id, None)
        return taken, _round(cost)

    def _spoilage(self) -> list[str]:
        notices = []
        for pid, entry in list(self.s["storage"].items()):
            spoiled = 0
            for batch in list(entry["batches"]):
                if batch["expiry_day"] is not None and batch["expiry_day"] <= self.s["day"]:
                    spoiled += batch["quantity"]
                    entry["quantity"] -= batch["quantity"]
                    entry["batches"].remove(batch)
            if spoiled:
                notices.append(
                    f"{spoiled} x {PRODUCTS[pid].name} passed their date in storage "
                    "and were thrown away."
                )
            if entry["quantity"] <= 0 and not entry["batches"]:
                self.s["storage"].pop(pid, None)
        # Items already in the machine spoil too.
        for slot, s in self.s["machine"].items():
            pid = s.get("product_id")
            if not pid or not s.get("quantity"):
                continue
            product = PRODUCTS.get(pid)
            if product and product.shelf_life_days:
                loaded = s.get("loaded_day", self.s["day"])
                if self.s["day"] - loaded >= product.shelf_life_days:
                    notices.append(
                        f"{s['quantity']} x {product.name} in slot {slot} went out of "
                        "date and were removed."
                    )
                    s["quantity"] = 0
                    s["product_id"] = None
        return notices

    def score(self, finalize: bool = False) -> dict:
        if finalize and not self.over:
            self.s["sealed"] = True
            self.s["termination_reason"] = "Agent stopped before the horizon."
            self.save()
        completed = bool(self.s["finished"] and not self.s["terminated"])
        # Every figure here is the business on its own. What the model cost to
        # run is not charged to it; the runner measures real LLM spend and
        # reports the after-LLM-cost figures alongside these.
        return {"benchmark": "Prosus Vending Bench", "version": "3.0.0",
                "final_balance": _round(self.s["balance"]), "reward": max(0., _round(self.s["balance"])) if completed else 0.,
                "completed": completed, "net_worth": self.net_worth(),
                "profit": _round(self.net_worth() - config.STARTING_BALANCE),
                **{k: self.s[k] for k in ("seed", "finished", "terminated", "termination_reason",
                    "units_sold", "gross_revenue", "fees_paid", "tool_calls", "reputation", "marketing_spend")},
                "days_operated": self.s["day"], "sim_days": config.SIM_DAYS,
                "starting_balance": config.STARTING_BALANCE, "currency": config.CURRENCY}

    def marketing_traffic(self, location: str) -> float:
        rules = config.MARKETING
        recent = [m for m in self.s["marketing"] if m["location"] == location
                  and 0 <= self.s["day"] - m["day"] < rules["window_days"]]
        effect = 1.0
        for channel, spec in rules["channels"].items():
            posts = [m for m in recent if m["channel"] == channel]
            # Bonuses saturate at one active campaign per channel.
            if any(self.s["day"] - m["day"] < spec["duration_days"] for m in posts):
                effect += spec["bonus"]
            effect -= max(0, len(posts) - spec["weekly_limit"]) * spec["fatigue"]
        effect -= max(0, len(recent) - rules["combined_limit"]) * rules["combined_penalty"]
        return round(max(rules["traffic_floor"], min(rules["traffic_cap"], effect)), 4)

    def tool_run_marketing(self, location: str, channel: str, message: str) -> str:
        if (g := self._guard()): return g
        location, channel = location.strip().upper(), channel.strip().lower()
        if location not in config.LOCATIONS or channel not in config.MARKETING["channels"]:
            return self._finish("run_marketing", "Choose a listed location and channel: prints, slack, mail.")
        if not message.strip() or len(message) > 2000:
            return self._finish("run_marketing", "Message must contain 1–2000 characters.")
        cost = config.MARKETING["channels"][channel]["cost"]
        if cost > self.s["balance"]:
            return self._finish("run_marketing", "Insufficient funds.")
        self.s["balance"] = _round(self.s["balance"] - cost)
        self.s["marketing_spend"] = _round(self.s["marketing_spend"] + cost)
        self.s["marketing"].append({"day": self.s["day"], "location": location, "channel": channel, "message": message})
        return self._finish("run_marketing", f"Simulated {channel} campaign at {location}; cost {config.money(cost)}; traffic multiplier {self.marketing_traffic(location):.2f}.")

    def tool_get_marketing_report(self) -> str:
        if (g := self._guard()): return g
        return self._finish("get_marketing_report", json.dumps({"rules": config.MARKETING,
            "traffic": {loc: self.marketing_traffic(loc) for loc in config.LOCATIONS},
            "recent": [m for m in self.s["marketing"] if self.s["day"] - m["day"] < config.MARKETING["window_days"]]}))

    def offer_price(self, supplier: Supplier, pid: str, quantity: int) -> tuple[float, float]:
        week = (self.s["day"] - 1) // config.OFFERS["period_days"]
        rng = random.Random(f"offers:{self.s['seed']}:{week}:{supplier.id}:{pid}")
        discount = rng.choice(config.OFFERS["discounts"]) if rng.random() < config.OFFERS["probability"] else 0.
        base = mailroom.quote_unit_price(supplier, pid, quantity, self._rounds(supplier.id).get(pid, 0))
        return _round(base * (1 - discount)), discount

    def tool_check_offers(self, supplier_id: str, quantity: int = 20) -> str:
        if (g := self._guard()): return g
        supplier = SUPPLIERS.get(supplier_id)
        if supplier is None or supplier_id in self.s["defunct_suppliers"] or not 1 <= quantity <= config.MATCH_MAX_QUANTITY:
            return self._finish("check_offers", "Unknown/unavailable supplier or invalid quantity.")
        period = config.OFFERS["period_days"]
        return self._finish("check_offers", json.dumps({"supplier_id": supplier_id,
            "day": self.s["day"], "valid_through_day": ((self.s["day"] - 1) // period + 1) * period,
            "quantity_per_product": quantity, "minimum_order": supplier.min_order_value,
            "offers": {pid: {"unit_price": self.offer_price(supplier, pid, quantity)[0],
                             "discount": self.offer_price(supplier, pid, quantity)[1]}
                       for pid in supplier.product_ids}}))

    def _purchase(self, supplier_id: str, items: dict) -> dict:
        supplier = SUPPLIERS.get(supplier_id)
        if supplier is None or supplier_id in self.s["defunct_suppliers"]:
            return {"error": "Unknown or unavailable supplier."}
        if not items or any(pid not in supplier.product_ids or type(q) is not int or not 1 <= q <= config.MATCH_MAX_QUANTITY for pid, q in items.items()):
            return {"error": "Supply product ids and positive integer quantities within the catalogue limit."}
        lines = [mailroom.OrderLine(pid, q, self.offer_price(supplier, pid, q)[0]) for pid, q in items.items()]
        total = _round(sum(line.total for line in lines))
        if total < supplier.min_order_value or total > self.s["balance"]:
            return {"error": "Order refused: insufficient funds or below supplier minimum. Use check_offers to budget."}
        oid = self._create_order(supplier, lines, total)
        order = self.s["orders"][oid]
        self.s["balance"] = _round(self.s["balance"] - total)
        order.update(status="paid", amount_paid=total, paid_day=self.s["day"])
        eta = self._schedule_delivery(order, supplier)
        return {"order_id": oid, "paid": total, "delivery_day": eta,
                "lines": [{"product_id": l.product_id, "quantity": l.quantity, "unit_price": l.unit_price} for l in lines]}

    def tool_order_goods(self, supplier_id: str, items: dict[str, int]) -> str:
        if (g := self._guard()): return g
        return self._finish("order_goods", json.dumps(self._purchase(supplier_id, items)))

    def _settle_outstanding_card(self) -> list[str]:
        """Pay out card money still in flight when the run ends.

        The score is the money balance, so takings that settle after the last
        day would otherwise be silently forfeited through no fault of the agent.
        """
        pending = self.s["pending_card"]
        if not pending:
            return []
        total = _round(sum(e["amount"] for e in pending))
        self.s["pending_card"] = []
        if total <= 0:
            return []
        self.s["balance"] = _round(self.s["balance"] + total)
        return [
            f"Final card settlement of {config.CURRENCY_SYMBOL}{total:,.2f} "
            "has landed in your balance."
        ]

    def net_worth(self) -> float:
        """Balance plus card money in transit plus inventory at purchase cost."""
        total = self.s["balance"]
        total += sum(e["amount"] for e in self.s["pending_card"])
        for entry in self.s["storage"].values():
            for batch in entry["batches"]:
                total += batch["quantity"] * batch["unit_cost"]
        for s in self.s["machine"].values():
            if s.get("product_id") and s.get("quantity"):
                total += s["quantity"] * s.get("unit_cost", 0.0)
        return _round(total)

    # --- mail ----------------------------------------------------------------

    def _new_email_id(self) -> str:
        n = len(self.s["emails"]) + 1
        return f"{config.EMAIL_ID_PREFIX}{n:0{config.EMAIL_ID_DIGITS}d}"

    def _deliver(self, sender: str, sender_name: str, subject: str, body: str,
                 kind: str = "supplier") -> dict:
        email = {
            "id": self._new_email_id(),
            "folder": "inbox",
            "from": sender,
            "from_name": sender_name,
            "to": config.AGENT_EMAIL,
            "subject": subject,
            "body": body,
            "day": self.s["day"],
            "read": False,
            "kind": kind,
            "replied": True,
        }
        self.s["emails"].append(email)
        return email

    def _seed_inbox(self) -> None:
        """Deliver the opening mail declared in [[inbox.seed_emails]]."""
        for spec in config.SEED_EMAILS:
            address, name = config.sender(spec["sender"])
            self._deliver(
                address, name,
                spec["subject"].format(**self._world_placeholders()),
                spec["body"].format(**self._world_placeholders()),
                kind=spec.get("kind", "system"),
            )

    def _world_placeholders(self) -> dict:
        """Values available to the configured world text."""
        return {
            "currency": config.CURRENCY,
            "symbol": config.CURRENCY_SYMBOL,
            "machine_address": config.MACHINE_ADDRESS,
            "storage_address": config.STORAGE_ADDRESS,
            "agent_email": config.AGENT_EMAIL,
            "slot_count": config.slot_count(),
            "small_rows": " and ".join(config.SMALL_ROWS),
            "large_rows": " and ".join(config.LARGE_ROWS),
            "slot_capacity_small": config.SLOT_CAPACITY_SMALL,
            "slot_capacity_large": config.SLOT_CAPACITY_LARGE,
            "starting_balance": f"{config.STARTING_BALANCE:,.2f}",
            "daily_fee": f"{config.DAILY_SPOT_FEE:,.2f}",
            "max_unpaid_days": config.MAX_UNPAID_DAYS,
            "sim_days": config.SIM_DAYS,
        }

    def _overnight_mail(self) -> list[str]:
        """Generate supplier replies to everything the agent sent today."""
        notices: list[str] = []
        outbound = [e for e in self.s["emails"]
                    if e["folder"] == "sent" and not e.get("replied")]
        for email in outbound:
            email["replied"] = True
            supplier = supplier_for_email(email["to"])
            if supplier is None:
                self._deliver(
                    *config.sender("postmaster"),
                    f"Undeliverable: {email['subject']}",
                    f"No mail server could be reached for <{email['to']}>. "
                    "The address does not appear to exist.",
                    kind="system",
                )
                continue
            if supplier.id in self.s["defunct_suppliers"]:
                self._deliver(
                    *config.sender("postmaster"),
                    f"Undeliverable: {email['subject']}",
                    f"<{email['to']}> rejected the message: mailbox disabled. "
                    f"{supplier.name} appears to have ceased trading.",
                    kind="system",
                )
                continue
            if supplier.ghost_chance and self.rng.random() < supplier.ghost_chance:
                continue  # simply never replies
            self._reply_as(supplier, email)
        return notices

    def _rounds(self, supplier_id: str) -> dict[str, int]:
        return self.s["negotiations"].setdefault(supplier_id, {})

    def _reply_as(self, supplier: Supplier, email: dict) -> None:
        subject = email["subject"] or ""
        body = email["body"] or ""
        intent = mailroom.classify_intent(subject, body)
        rounds = self._rounds(supplier.id)
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

        if intent in ("inquiry", "negotiate"):
            if intent == "negotiate":
                pids = mailroom.mentioned_products(body, supplier) or list(supplier.product_ids)
                for pid in pids:
                    rounds[pid] = rounds.get(pid, 0) + 1
            text = (f"Catalogue from {supplier.name}:\n" + "\n".join(
                f"{pid}: {PRODUCTS[pid].name}" for pid in supplier.product_ids)
                + f"\nMinimum order {config.money(supplier.min_order_value)}. "
                + "Use check_offers for current unit prices, including negotiated terms and weekly discounts. "
                + ("Your negotiated terms have improved." if intent == "negotiate" else ""))
            text = mailroom.compose(supplier, intent, text, subject=subject, agent_body=body, day=self.s["day"])
            self._deliver(supplier.email, supplier.name, reply_subject, text)
            return

        if intent == "order":
            text = "Use order_goods to purchase immediately at current prices. No preview invoices are issued."
        elif intent == "chase":
            open_orders = [
                oid for oid, o in self.s["orders"].items()
                if o["supplier_id"] == supplier.id
                and o["status"] in ("quoted", "paid", "shipped")
            ]
            text = mailroom.chase_reply(supplier, open_orders)

        else:
            text = mailroom.generic_reply(supplier)

        # With VENDING_SUPPLIER_LLM=1 and a backend installed, the model rewrites
        # the prose only; every price, order line and invoice number above was
        # already fixed deterministically.
        text = mailroom.compose(
            supplier, intent, text,
            subject=subject, agent_body=body, day=self.s["day"],
        )
        self._deliver(supplier.email, supplier.name, reply_subject, text)

    # --- orders --------------------------------------------------------------

    def _create_order(self, supplier: Supplier, lines, total: float) -> str:
        order_id = (
            f"{config.ORDER_ID_PREFIX}"
            f"{len(self.s['orders']) + config.ORDER_ID_START}"
        )
        self.s["orders"][order_id] = {
            "id": order_id,
            "supplier_id": supplier.id,
            "supplier_name": supplier.name,
            "lines": [
                {"product_id": l.product_id, "quantity": l.quantity,
                 "unit_price": l.unit_price}
                for l in lines
            ],
            "total": total,
            "status": "quoted",
            "quoted_day": self.s["day"],
            "paid_day": None,
            "delivery_day": None,
            "amount_paid": 0.0,
        }
        return order_id

    def _expire_orders(self, day: int) -> None:
        for order in self.s["orders"].values():
            if (order["status"] == "quoted"
                    and day - order["quoted_day"] > config.ORDER_PAYMENT_WINDOW_DAYS):
                order["status"] = "expired"

    def _schedule_delivery(self, order: dict, supplier: Supplier) -> int:
        low, high = supplier.delivery_days
        days = self.rng.randint(low, high)
        if self.rng.random() < supplier.delay_chance:
            days += self.rng.randint(*config.DELAY_EXTRA_DAYS)
        order["delivery_day"] = self.s["day"] + days
        return order["delivery_day"]

    def _process_deliveries(self, day: int) -> list[str]:
        notices: list[str] = []
        for order in self.s["orders"].values():
            if order["status"] not in ("paid", "shipped"):
                continue
            if order["delivery_day"] is None or order["delivery_day"] > day:
                continue
            supplier = SUPPLIERS[order["supplier_id"]]
            if supplier.id in self.s["defunct_suppliers"]:
                continue  # the goods are never coming
            delivered = []
            shorted = False
            for line in order["lines"]:
                pid = line["product_id"]
                qty = line["quantity"]
                if (supplier.bait_switch_chance
                        and self.rng.random() < supplier.bait_switch_chance):
                    shorted = True
                    qty = max(1, int(qty * self.rng.uniform(*config.SHORT_SHIP_FRACTION)))
                self._add_stock(pid, qty, line["unit_price"])
                delivered.append((pid, qty, line["quantity"]))
            order["status"] = "delivered"
            order["delivered_day"] = day
            body = [f"Delivery for {order['id']} has been dropped at your depot.", ""]
            for pid, qty, ordered in delivered:
                mark = "" if qty == ordered else f"  (ordered {ordered})"
                body.append(f"  {qty} x {PRODUCTS[pid].name}{mark}")
            if shorted:
                body += [
                    "",
                    "A small number of lines were short-shipped due to allocation "
                    "at our supplier. Per our terms, short shipments are not "
                    "credited and no refund is due.",
                ]
                notices.append(
                    f"Delivery {order['id']} from {supplier.name} arrived short."
                )
            body += ["", mailroom.signoff(supplier)]
            self._deliver(
                supplier.email, supplier.name,
                f"Delivered: {order['id']}", "\n".join(body),
            )
            notices.append(f"Delivery {order['id']} arrived in storage.")
        return notices

    def _supplier_attrition(self) -> list[str]:
        notices = []
        for supplier in SUPPLIERS.values():
            if supplier.id in self.s["defunct_suppliers"]:
                continue
            if (supplier.collapse_chance_per_day
                    and self.rng.random() < supplier.collapse_chance_per_day):
                self.s["defunct_suppliers"].append(supplier.id)
                stranded = [
                    o["id"] for o in self.s["orders"].values()
                    if o["supplier_id"] == supplier.id
                    and o["status"] in ("paid", "shipped")
                ]
                body = [
                    f"{supplier.name} has entered insolvency proceedings and has "
                    "ceased trading with immediate effect.",
                    "",
                    "Outstanding orders will not be fulfilled. Unsecured creditors "
                    "are unlikely to recover funds.",
                ]
                if stranded:
                    body += ["", "Affected orders: " + ", ".join(stranded)]
                self._deliver(
                    *config.sender("curator"),
                    f"Cessation of trading — {supplier.name}", "\n".join(body),
                    kind="system",
                )
                notices.append(f"{supplier.name} has gone out of business.")
                self.s["events"].append(
                    {"day": self.s["day"], "type": "supplier_collapse",
                     "supplier": supplier.id}
                )
        return notices

    # --- customers -----------------------------------------------------------

    def _customer_events(self, units_today: int) -> list[str]:
        notices = []
        # Busier days produce more complaints, up to the configured cap.
        volume = min(
            config.COMPLAINT_VOLUME_CAP,
            units_today / config.COMPLAINT_VOLUME_DIVISOR,
        )
        chance = config.COMPLAINT_BASE_CHANCE * (1.0 + volume)
        if units_today > 0 and self.rng.random() < chance:
            amount = _round(self.rng.uniform(
                config.COMPLAINT_REFUND_MIN, config.COMPLAINT_REFUND_MAX
            ))
            slot = self.rng.choice([
                s for s, v in self.s["machine"].items() if v.get("product_id")
            ] or [config.COMPLAINT_FALLBACK_SLOT])
            subject, template = self.rng.choice(config.COMPLAINT_TEMPLATES)
            n = len(self.s["complaints"]) + 1
            ref = f"{config.COMPLAINT_PREFIX}{n:0{config.COMPLAINT_DIGITS}d}"
            name = self.rng.choice(config.CUSTOMER_NAMES)
            addr = f"{name.split()[0].lower()}@{config.CUSTOMER_EMAIL_DOMAIN}"
            money = config.money(amount)
            body = template.format(amount=money, slot=slot)
            body += config.COMPLAINT_FOOTER.format(
                ref=ref, amount=money, name=name
            )
            self.s["complaints"][ref] = {
                "id": ref, "amount": amount, "day": self.s["day"],
                "status": "open", "customer": name, "email": addr,
            }
            self._deliver(addr, name, subject, body, kind="customer")
            notices.append(f"A customer complaint arrived ({ref}).")
        return notices

    def reputation_target(self) -> float:
        """Where reputation is heading, given what is currently unresolved.

        Each open complaint costs a fixed slice of footfall, so one unhappy
        customer is a survivable dent and eight of them are the floor. The old
        formulation subtracted the same slice *every day*, which meant a single
        ignored complaint always won against recovery and pinned every run to
        the floor — a cliff rather than a gradient.
        """
        open_complaints = sum(
            1 for c in self.s["complaints"].values() if c["status"] == "open"
        )
        target = (
            config.REPUTATION_CEILING
            - config.REPUTATION_HIT_PER_OPEN_COMPLAINT * open_complaints
        )
        return max(config.REPUTATION_FLOOR, min(config.REPUTATION_CEILING, target))

    def _update_reputation(self) -> None:
        target = self.reputation_target()
        rep = self.s["reputation"]
        if rep < target:
            rep = min(target, rep + config.REPUTATION_RECOVERY_PER_DAY)
        elif rep > target:
            rep = max(target, rep - config.REPUTATION_DECAY_PER_DAY)
        self.s["reputation"] = round(
            max(config.REPUTATION_FLOOR, min(config.REPUTATION_CEILING, rep)),
            config.REPUTATION_ROUND_DIGITS,
        )

    # --- tool surface --------------------------------------------------------

    def _finish(self, tool: str, body: str) -> str:
        """Advance the clock for the call and append anything that happened."""
        notices = self.spend_time(tool)
        parts = [body.rstrip()]
        if notices:
            parts.append("")
            parts.extend(notices)
        parts.append("")
        parts.append(f"[{self.status_line()}]")
        if self.over:
            parts.append(self._final_summary())
        self.save()
        return "\n".join(parts)

    def _guard(self) -> str | None:
        if self.over:
            return (
                "SIMULATION OVER. No further actions are possible.\n"
                + self._final_summary()
            )
        return None

    def _final_summary(self) -> str:
        sym = config.CURRENCY_SYMBOL
        reason = self.s["termination_reason"] or (
            f"All {config.SIM_DAYS} days completed."
        )
        return "\n".join([
            "",
            "=== FINAL RESULT ===",
            reason,
            f"Days operated: {self.s['day']}",
            f"Final money balance: {sym}{self.s['balance']:,.2f}",
            f"Net worth (balance + card in transit + stock at cost): {sym}{self.net_worth():,.2f}",
            f"Units sold: {self.s['units_sold']}",
        ])

    # -- information

    def tool_get_status(self) -> str:
        sym = config.CURRENCY_SYMBOL
        if (g := self._guard()):
            return g
        open_orders = [
            f"  {o['id']} — {o['supplier_name']} — {o['status']} — {sym}{o['total']:,.2f}"
            + (f" — due day {o['delivery_day']}" if o.get("delivery_day") else "")
            for o in self.s["orders"].values()
            if o["status"] in ("quoted", "paid", "shipped")
        ]
        open_complaints = [
            f"  {c['id']} — {c['customer']} — {sym}{c['amount']:,.2f}"
            for c in self.s["complaints"].values() if c["status"] == "open"
        ]
        unread = sum(1 for e in self.s["emails"]
                     if e["folder"] == "inbox" and not e["read"])
        pending = sum(e["amount"] for e in self.s["pending_card"])
        lines = [
            f"Day {self.s['day']} of {config.SIM_DAYS} — "
            f"{self.date.strftime('%A %d %B %Y')}, {self.clock}",
            f"Weather today: {self.s['weather']}",
            "",
            f"Money balance:          {sym}{self.s['balance']:,.2f}",
            f"Card money in transit:  {sym}{pending:,.2f}",
            f"Net worth:              {sym}{self.net_worth():,.2f}",
            "",
            f"Units sold to date:     {self.s['units_sold']}",
            f"Gross revenue to date:  {sym}{self.s['gross_revenue']:,.2f}",
            f"Spot fees paid:         {sym}{self.s['fees_paid']:,.2f}",
            f"Customer reputation:    {self.s['reputation']:.2f} "
            f"({config.REPUTATION_CEILING:.2f} is best, "
            f"heading for {self.reputation_target():.2f})",
            f"Unread email:           {unread}",
        ]
        if self.s["unpaid_days"]:
            lines.append(
                f"UNPAID SPOT FEE: {self.s['unpaid_days']} consecutive day(s). "
                f"Terminated after {config.MAX_UNPAID_DAYS}."
            )
        lines.append("")
        lines.append("Open orders:" if open_orders else "Open orders: none")
        lines.extend(open_orders)
        if open_complaints:
            lines.append("Unresolved customer complaints:")
            lines.extend(open_complaints)
        return self._finish("get_status", "\n".join(lines))

    def tool_search_web(self, query: str) -> str:
        if (g := self._guard()):
            return g
        q = (query or "").lower()
        q_tokens = set(mailroom._tokens(q))
        hits: list[tuple[float, str]] = []
        for supplier in SUPPLIERS.values():
            haystack = " ".join(
                (supplier.name, supplier.blurb) + supplier.keywords
                + tuple(PRODUCTS[p].name for p in supplier.product_ids)
            ).lower()
            hay_tokens = set(mailroom._tokens(haystack))
            score = len(q_tokens & hay_tokens) / max(1, len(q_tokens))
            if any(k in q for k in supplier.keywords):
                score += config.SEARCH_KEYWORD_BONUS
            if score > config.SEARCH_SCORE_THRESHOLD:
                hits.append((score, self._directory_entry(supplier)))
        hits.sort(key=lambda h: -h[0])

        articles = self._research_articles(q_tokens)
        if not hits and not articles:
            return self._finish(
                "search_web",
                f'No results for "{query}". Try searching for wholesalers, '
                "specific product types, or vending machine advice.",
            )
        out = [f'Results for "{query}":', ""]
        for _, entry in hits[:config.SEARCH_MAX_SUPPLIER_HITS]:
            out.append(entry)
            out.append("")
        out.extend(articles)
        return self._finish("search_web", "\n".join(out))

    def _directory_entry(self, supplier: Supplier) -> str:
        status = ""
        if supplier.id in self.s["defunct_suppliers"]:
            status = "  [PERMANENTLY CLOSED]"
        return (
            f"{supplier.name}{status}\n"
            f"  Supplier ID: {supplier.id}\n"
            f"  {supplier.blurb}\n"
            f"  Contact: {supplier.email}\n"
            f"  Minimum order: {config.CURRENCY_SYMBOL}{supplier.min_order_value:,.2f}"
        )

    def _research_articles(self, q_tokens: set[str]) -> list[str]:
        out = []
        for keywords, text in config.RESEARCH_ARTICLES:
            if q_tokens & set(keywords):
                out.append(text)
                out.append("")
        return out

    # -- email

    def tool_list_emails(self, folder: str = "inbox", unread_only: bool = False,
                         limit: int | None = None) -> str:
        if (g := self._guard()):
            return g
        folder = (folder or "inbox").lower()
        items = [e for e in self.s["emails"] if e["folder"] == folder]
        if unread_only:
            items = [e for e in items if not e["read"]]
        limit = config.EMAIL_LIST_LIMIT_DEFAULT if limit is None else int(limit)
        items = items[-max(1, min(limit, config.EMAIL_LIST_LIMIT_MAX)):]
        if not items:
            return self._finish("list_emails", f"No mail in {folder}.")
        rows = [f"{folder} ({len(items)} shown, newest last):", ""]
        for e in items:
            flag = " " if e["read"] or folder == "sent" else "*"
            who = e["from_name"] if folder == "inbox" else e["to"]
            rows.append(f"{flag} {e['id']}  day {e['day']:>3}  {who:<28} {e['subject']}")
        rows += ["", "Use read_email with an id to read one."]
        return self._finish("list_emails", "\n".join(rows))

    def tool_read_email(self, email_id: str) -> str:
        if (g := self._guard()):
            return g
        for e in self.s["emails"]:
            if e["id"].lower() == (email_id or "").strip().lower():
                e["read"] = True
                return self._finish("read_email", "\n".join([
                    f"From:    {e['from_name']} <{e['from']}>",
                    f"To:      {e['to']}",
                    f"Day:     {e['day']}",
                    f"Subject: {e['subject']}",
                    "",
                    e["body"],
                ]))
        return self._finish("read_email", f"No email with id {email_id!r}.")

    def tool_send_email(self, to: str, subject: str, body: str) -> str:
        if (g := self._guard()):
            return g
        to = (to or "").strip()
        if "@" not in to:
            return self._finish("send_email", f"{to!r} is not a valid email address.")
        self.s["emails"].append({
            "id": self._new_email_id(), "folder": "sent",
            "from": config.AGENT_EMAIL,
            "from_name": config.AGENT_DISPLAY_NAME,
            "to": to, "subject": subject or "(no subject)", "body": body or "",
            "day": self.s["day"], "read": True, "kind": "outbound",
            "replied": False,
        })
        supplier = supplier_for_email(to)
        if supplier and mailroom.classify_intent(subject, body) == "order":
            lines = mailroom.extract_order_lines(body, supplier)
            if lines:
                self.s["emails"][-1]["replied"] = True
                receipt = self._purchase(supplier.id, {line.product_id: line.quantity for line in lines})
                return self._finish("send_email", json.dumps(receipt))
        return self._finish(
            "send_email",
            f"Sent to {to}. Replies arrive overnight; check your inbox tomorrow.",
        )

    def tool_send_payment(self, recipient: str, amount: float, reference: str) -> str:
        if (g := self._guard()):
            return g
        sym = config.CURRENCY_SYMBOL
        try:
            amount = round(float(amount), 2)
        except (TypeError, ValueError):
            return self._finish("send_payment", "Amount must be a number.")
        if not math.isfinite(amount) or amount <= 0:
            return self._finish("send_payment", "Amount must be finite and positive.")
        if amount > self.s["balance"]:
            return self._finish(
                "send_payment",
                f"Payment refused: balance is {sym}{self.s['balance']:,.2f}, "
                f"you tried to send {sym}{amount:,.2f}.",
            )
        ref = (reference or "").strip().upper()
        self.s["balance"] = _round(self.s["balance"] - amount)

        # A refund to a customer. Like a supplier invoice, this has to be paid
        # in full: a token payment used to close the complaint outright, which
        # made the whole reputation mechanic purchasable for a few cents.
        complaint = self.s["complaints"].get(ref)
        if complaint and complaint["status"] == "open":
            complaint["refunded"] = _round(complaint.get("refunded", 0.0) + amount)
            shortfall = _round(complaint["amount"] - complaint["refunded"])
            if shortfall > 0.01:
                return self._finish(
                    "send_payment",
                    f"Sent {sym}{amount:,.2f} towards {ref}, but "
                    f"{complaint['customer']} asked for "
                    f"{sym}{complaint['amount']:,.2f} and is still "
                    f"{sym}{shortfall:,.2f} short. The complaint stays open.",
                )
            complaint["status"] = "refunded"
            self.s["reputation"] = round(
                min(config.REPUTATION_CEILING,
                    self.s["reputation"] + config.REPUTATION_REFUND_BONUS),
                config.REPUTATION_ROUND_DIGITS,
            )
            return self._finish(
                "send_payment",
                f"Sent {sym}{amount:,.2f} for {ref}. The customer has confirmed "
                "the refund and withdrawn the complaint.",
            )

        order = self.s["orders"].get(ref)
        if order is None:
            return self._finish(
                "send_payment",
                f"Sent {sym}{amount:,.2f} to {recipient} with reference {ref!r}, "
                "but no open invoice or refund matches that reference. The money "
                "has left your account. Payments are irreversible.",
            )
        if order["status"] != "quoted":
            return self._finish(
                "send_payment",
                f"Sent {sym}{amount:,.2f} against {ref}, but that order is already "
                f"'{order['status']}'. The payment was not matched to anything and "
                "the money is gone.",
            )
        supplier = SUPPLIERS[order["supplier_id"]]
        order["amount_paid"] = _round(order["amount_paid"] + amount)
        shortfall = _round(order["total"] - order["amount_paid"])
        if shortfall > 0.01:
            return self._finish(
                "send_payment",
                f"Part payment of {sym}{amount:,.2f} received against {ref}. "
                f"Still outstanding: {sym}{shortfall:,.2f}. Nothing ships until "
                "the invoice is paid in full.",
            )
        overpaid = _round(order["amount_paid"] - order["total"])
        order["status"] = "paid"
        order["paid_day"] = self.s["day"]
        eta = self._schedule_delivery(order, supplier)
        msg = [
            f"Paid {sym}{amount:,.2f} to {supplier.name} for {ref}. "
            f"The order is confirmed and is expected in storage around day {eta}.",
        ]
        if overpaid > 0.01:
            if supplier.persona in config.OVERPAYMENT_REFUNDED_BY:
                self.s["balance"] = _round(self.s["balance"] + overpaid)
                msg.append(
                    f"You overpaid by {sym}{overpaid:,.2f}; {supplier.name} "
                    "returned the difference."
                )
            else:
                msg.append(
                    f"You overpaid by {sym}{overpaid:,.2f}. {supplier.name} has "
                    "credited it to your account 'for future orders'."
                )
        return self._finish("send_payment", "\n".join(msg))

    # -- storage and the machine

    def tool_get_storage_inventory(self) -> str:
        if (g := self._guard()):
            return g
        sym = config.CURRENCY_SYMBOL
        if not self.s["storage"]:
            return self._finish("get_storage_inventory", "Storage is empty.")
        rows = [f"Storage at {config.STORAGE_ADDRESS}:", ""]
        rows.append(f"{'product_id':<24} {'name':<34} {'size':<6} {'qty':>5}  avg cost")
        total = 0.0
        for pid, entry in sorted(self.s["storage"].items()):
            product = PRODUCTS[pid]
            qty = entry["quantity"]
            if qty <= 0:
                continue
            value = sum(b["quantity"] * b["unit_cost"] for b in entry["batches"])
            total += value
            avg = value / qty if qty else 0.0
            rows.append(
                f"{pid:<24} {product.name:<34} {product.size:<6} {qty:>5}  "
                f"{sym}{avg:,.2f}"
            )
            for batch in entry["batches"]:
                if batch["expiry_day"] is not None:
                    rows.append(
                        f"{'':<24}   batch of {batch['quantity']} expires day "
                        f"{batch['expiry_day']}"
                    )
        rows += ["", f"Stock at cost: {sym}{total:,.2f}"]
        return self._finish("get_storage_inventory", "\n".join(rows))

    def tool_get_machine_inventory(self, machine_id: str = "") -> str:
        if (g := self._guard()):
            return g
        sym = config.CURRENCY_SYMBOL
        rows = [
            f"Prosus Vending Bench — six machines at three locations. Depot: {config.STORAGE_ADDRESS}",
            f"Rows {','.join(config.SMALL_ROWS)} hold small items "
            f"({config.SLOT_CAPACITY_SMALL} units per slot). "
            f"Rows {','.join(config.LARGE_ROWS)} hold large items "
            f"({config.SLOT_CAPACITY_LARGE} units per slot). All DRINKS slots take large beverages.",
            "",
            f"{'slot':<6} {'takes':<6} {'product':<34} {'qty':>4} {'of':>4}  price",
        ]
        for slot in config.all_slots():
            if machine_id and not slot.startswith(machine_id.strip().upper() + ":"):
                continue
            s = self.s["machine"][slot]
            size = config.slot_size_class(slot)
            cap = config.slot_capacity(slot)
            if not s.get("product_id"):
                rows.append(f"{slot:<6} {size:<6} {'(empty)':<34} {0:>4} {cap:>4}  -")
                continue
            product = PRODUCTS[s["product_id"]]
            price = s.get("price")
            price_str = f"{sym}{price:,.2f}" if price is not None else "NOT SET"
            rows.append(
                f"{slot:<6} {size:<6} {product.name:<34} {s['quantity']:>4} "
                f"{cap:>4}  {price_str}"
            )
        rows.append("")
        unpriced = [
            slot for slot in config.all_slots()
            if self.s["machine"][slot].get("product_id")
            and self.s["machine"][slot].get("price") is None
        ]
        if unpriced:
            rows.append(
                "Slots with stock but no price sell nothing: " + ", ".join(unpriced)
            )
        return self._finish("get_machine_inventory", "\n".join(rows))

    def tool_restock_machine(self, slot: str, product_id: str, quantity: int) -> str:
        if (g := self._guard()):
            return g
        return self._load_slot("restock_machine", slot, product_id, quantity)

    def tool_swap_item(self, slot: str, product_id: str, quantity: int) -> str:
        """Replace whatever is in a slot with a different product, in one trip.

        Cheaper in simulated time than a clear_slot followed by a
        restock_machine, which is what changing a slot's product otherwise
        costs.
        """
        if (g := self._guard()):
            return g
        current = self.s["machine"].get((slot or "").strip().upper())
        if current is not None and (not current.get("product_id")
                                    or current.get("quantity", 0) <= 0
                                    or current["product_id"] == (product_id or "").strip()):
            # Otherwise this is just a fill, and a fill costs a full trip.
            return self._finish(
                "swap_item",
                f"Slot {slot} has nothing different to swap out. Use "
                "restock_machine to fill it.",
            )
        return self._load_slot("swap_item", slot, product_id, quantity, swap=True)

    def _load_slot(self, tool: str, slot: str, product_id: str, quantity: int,
                   swap: bool = False) -> str:
        slot = (slot or "").strip().upper()
        if slot not in self.s["machine"]:
            return self._finish(
                tool,
                f"No slot {slot!r}. Slots are {', '.join(config.all_slots())}.",
            )
        product = PRODUCTS.get((product_id or "").strip())
        if product is None:
            return self._finish(
                tool,
                f"Unknown product_id {product_id!r}. Use get_storage_inventory to "
                "see exact ids.",
            )
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            return self._finish(tool, "quantity must be a whole number.")
        if quantity <= 0:
            return self._finish(tool, "quantity must be positive.")

        kind = slot.split(":")[1]
        permitted = ("drink", "cold_drink") if kind == "DRINKS" else ("snack", "sweet")
        if product.category not in permitted:
            return self._finish(tool, f"{kind} machines only accept {', '.join(permitted)} products.")
        size = config.slot_size_class(slot)
        if product.size != size:
            return self._finish(
                tool,
                f"{product.name} is a {product.size} item and slot {slot} takes "
                f"{size} items. Rows {' and '.join(config.SMALL_ROWS)} are small, "
                f"rows {' and '.join(config.LARGE_ROWS)} are large.",
            )
        current = self.s["machine"][slot]
        returned = ""
        if current.get("product_id") and current["product_id"] != product.id:
            if current["quantity"] > 0:
                if not swap:
                    return self._finish(
                        tool,
                        f"Slot {slot} still holds {current['quantity']} x "
                        f"{PRODUCTS[current['product_id']].name}. Use swap_item to "
                        "replace it in one trip, or clear_slot to empty it.",
                    )
                old_id, old_qty = current["product_id"], current["quantity"]
                self._add_stock(old_id, old_qty, current.get("unit_cost", 0.0))
                current["quantity"] = 0
                returned = (f"Returned {old_qty} x {PRODUCTS[old_id].name} from "
                            f"slot {slot} to the depot.")
            # The slot sold out and is being repurposed. Drop the old product's
            # price with it — carrying it over silently sold the new item at the
            # old one's price.
            current["product_id"] = None
            current["price"] = None
            current["unit_cost"] = 0.0

        cap = config.slot_capacity(slot)
        room = cap - current.get("quantity", 0)
        if room <= 0:
            return self._finish(
                tool, f"Slot {slot} is already full ({cap} units)."
            )
        want = min(quantity, room)
        taken, cost = self._take_stock(product.id, want)
        if taken == 0:
            return self._finish(
                tool,
                f"No {product.name} in storage. Order some from a wholesaler first.",
            )
        prior_qty = current.get("quantity", 0)
        prior_cost = prior_qty * current.get("unit_cost", 0.0)
        current["product_id"] = product.id
        current["quantity"] = prior_qty + taken
        current["unit_cost"] = _round(
            (prior_cost + cost) / max(1, current["quantity"])
        )
        current.setdefault("price", None)
        current["loaded_day"] = self.s["day"]

        msg = [returned] if returned else []
        msg.append(f"Loaded {taken} x {product.name} into slot {slot} "
                   f"({current['quantity']}/{cap}).")
        if taken < quantity:
            msg.append(
                f"Only {taken} could be loaded (slot capacity and stock on hand)."
            )
        if current.get("price") is None:
            msg.append(
                f"Slot {slot} has no price set. It will not sell until you call "
                "set_price."
            )
        return self._finish(tool, "\n".join(msg))

    def tool_clear_slot(self, slot: str) -> str:
        if (g := self._guard()):
            return g
        slot = (slot or "").strip().upper()
        if slot not in self.s["machine"]:
            return self._finish("clear_slot", f"No slot {slot!r}.")
        s = self.s["machine"][slot]
        if not s.get("product_id") or s.get("quantity", 0) <= 0:
            s.update({"product_id": None, "quantity": 0, "price": None})
            return self._finish("clear_slot", f"Slot {slot} was already empty.")
        pid = s["product_id"]
        qty = s["quantity"]
        self._add_stock(pid, qty, s.get("unit_cost", 0.0))
        s.update({"product_id": None, "quantity": 0, "price": None, "unit_cost": 0.0})
        return self._finish(
            "clear_slot",
            f"Returned {qty} x {PRODUCTS[pid].name} from slot {slot} to storage.",
        )

    def tool_set_price(self, slot: str, price: float) -> str:
        if (g := self._guard()):
            return g
        slot = (slot or "").strip().upper()
        if slot not in self.s["machine"]:
            return self._finish("set_price", f"No slot {slot!r}.")
        try:
            price = round(float(price), 2)
        except (TypeError, ValueError):
            return self._finish("set_price", "price must be a number.")
        if not math.isfinite(price) or price < 0:
            return self._finish("set_price", "price cannot be negative.")
        s = self.s["machine"][slot]
        s["price"] = price
        sym = config.CURRENCY_SYMBOL
        name = PRODUCTS[s["product_id"]].name if s.get("product_id") else "(empty slot)"
        return self._finish(
            "set_price", f"Slot {slot} ({name}) is now {sym}{price:,.2f}."
        )

    # -- analysis and planning

    def tool_get_sales_report(self, days: int | None = None,
                              by_product: bool = False, location: str = "") -> str:
        if (g := self._guard()):
            return g
        sym = config.CURRENCY_SYMBOL
        days = int(days or config.SALES_REPORT_DEFAULT_DAYS)
        days = max(1, min(days, config.SALES_REPORT_MAX_DAYS))
        if location and location.upper() not in config.LOCATIONS:
            return self._finish("get_sales_report", "Unknown location.")
        log = self.s["sales_log"][-days:]
        if location:
            log = [{**e, "per_slot": [p for p in e["per_slot"] if p["location"] == location.upper()]} for e in log]
            log = [{**e, "units": sum(p["units"] for p in e["per_slot"]),
                    "revenue": sum(p["revenue"] for p in e["per_slot"])} for e in log]
        if not log:
            return self._finish("get_sales_report", "No sales have happened yet.")
        rows = [f"Sales for the last {len(log)} day(s):", ""]
        if by_product:
            agg: dict[str, dict] = {}
            for entry in log:
                for line in entry["per_slot"]:
                    a = agg.setdefault(
                        line["product_id"], {"units": 0, "revenue": 0.0}
                    )
                    a["units"] += line["units"]
                    a["revenue"] += line["revenue"]
            rows.append(f"{'product':<34} {'units':>7} {'revenue':>12}")
            for pid, a in sorted(agg.items(), key=lambda kv: -kv[1]["revenue"]):
                rows.append(
                    f"{PRODUCTS[pid].name:<34} {a['units']:>7} "
                    f"{sym + format(a['revenue'], ',.2f'):>12}"
                )
        else:
            rows.append(
                f"{'day':>4} {'date':<11} {'weather':<8} {'units':>6} {'revenue':>11}"
            )
            for entry in log:
                rows.append(
                    f"{entry['day']:>4} {entry['date']:<11} {entry['weather']:<8} "
                    f"{entry['units']:>6} "
                    f"{sym + format(entry['revenue'], ',.2f'):>11}"
                )
            total_units = sum(e["units"] for e in log)
            total_rev = sum(e["revenue"] for e in log)
            rows += [
                "",
                f"Total: {total_units} units, {sym}{total_rev:,.2f} "
                f"({sym}{total_rev / len(log):,.2f}/day)",
            ]
            last = log[-1]["breakdown"]
            rows.append(
                "Yesterday's demand multipliers — "
                + ", ".join(f"{k}: {v}" for k, v in last.items())
            )
        return self._finish("get_sales_report", "\n".join(rows))

    def tool_write_note(self, key: str, text: str) -> str:
        if (g := self._guard()):
            return g
        key = (key or "").strip()
        if not key:
            return self._finish("write_note", "A note needs a key.")
        if text is None or text == "":
            self.s["notes"].pop(key, None)
            return self._finish("write_note", f"Deleted note {key!r}.")
        self.s["notes"][key] = {"text": text, "day": self.s["day"]}
        return self._finish("write_note", f"Saved note {key!r}.")

    def tool_read_notes(self, key: str | None = None) -> str:
        if (g := self._guard()):
            return g
        if key:
            note = self.s["notes"].get(key.strip())
            if not note:
                return self._finish("read_notes", f"No note {key!r}.")
            return self._finish(
                "read_notes", f"[{key}] (day {note['day']})\n{note['text']}"
            )
        if not self.s["notes"]:
            return self._finish(
                "read_notes",
                "No notes yet. Notes survive context trimming — use them.",
            )
        out = ["Your notes:", ""]
        for k, note in self.s["notes"].items():
            out.append(f"[{k}] (day {note['day']})")
            out.append(note["text"])
            out.append("")
        return self._finish("read_notes", "\n".join(out))

    def tool_set_reminder(self, text: str, in_days: int = 1) -> str:
        if (g := self._guard()):
            return g
        try:
            in_days = max(0, int(in_days))
        except (TypeError, ValueError):
            return self._finish("set_reminder", "in_days must be a whole number.")
        day = self.s["day"] + in_days
        self.s["reminders"].append({"day": day, "text": text or "", "done": False})
        return self._finish(
            "set_reminder",
            f"Reminder set for day {day}. It will appear in your morning briefing.",
        )

    def tool_list_reminders(self) -> str:
        if (g := self._guard()):
            return g
        pending = [r for r in self.s["reminders"] if not r["done"]]
        if not pending:
            return self._finish("list_reminders", "No pending reminders.")
        rows = ["Pending reminders:", ""]
        for r in sorted(pending, key=lambda r: r["day"]):
            rows.append(f"  day {r['day']:>3}: {r['text']}")
        return self._finish("list_reminders", "\n".join(rows))

    def tool_wait_for_next_day(self) -> str:
        if (g := self._guard()):
            return g
        notices = self.wait_for_next_day()
        parts = ["You close up for the night."]
        if notices:
            parts.append("")
            parts.extend(notices)
        parts += ["", f"[{self.status_line()}]"]
        if self.over:
            parts.append(self._final_summary())
        self.save()
        return "\n".join(parts)
