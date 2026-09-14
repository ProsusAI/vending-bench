"""Tests for the Vending-Bench simulation engine.

Run with:  python3 -m pytest tests/ -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tasks" / "vending-bench" / "environment" / "sim-server"))
sys.path.insert(0, str(ROOT / "tasks" / "vending-bench" / "solution"))

sys.path.insert(0, str(ROOT / "scripts"))

from vending import config, demand, mailroom  # noqa: E402
from vending.catalog import PRODUCTS, SUPPLIERS  # noqa: E402
from vending.engine import Engine  # noqa: E402
from reference_bot import Bot  # noqa: E402
from local_run import DirectClient  # noqa: E402
import sync_config  # noqa: E402


@pytest.fixture
def engine():
    return Engine.new(seed=99)


# --- catalogue integrity -----------------------------------------------------

def test_every_supplier_sells_real_products():
    for supplier in SUPPLIERS.values():
        for pid in supplier.product_ids:
            assert pid in PRODUCTS, f"{supplier.id} references unknown product {pid}"


def test_products_are_profitable_at_the_reference_price():
    """A supplier's best negotiated price must leave room to make money."""
    for supplier in SUPPLIERS.values():
        for pid in supplier.product_ids:
            best = mailroom.floor_price(supplier, pid)
            assert best >= PRODUCTS[pid].true_cost * 0.9, (
                f"{supplier.id} can be pushed below cost on {pid}"
            )


def test_at_least_one_supplier_is_cheaper_than_the_adversarial_one():
    shark = SUPPLIERS["premium-vend-partners"]
    honest = SUPPLIERS["kanaal-cash-carry"]
    pid = "crisps-paprika-335g"
    assert mailroom.floor_price(honest, pid) < mailroom.floor_price(shark, pid)


# --- demand model ------------------------------------------------------------

def test_price_impact_is_one_at_the_reference_price():
    assert demand.price_impact(4.50, 4.50, -1.0) == 1.0


def test_raising_the_price_reduces_demand():
    high = demand.price_impact(6.00, 4.50, -1.0)
    low = demand.price_impact(3.00, 4.50, -1.0)
    assert low > 1.0 > high >= 0.0


def test_demand_cannot_go_negative():
    assert demand.price_impact(1000.0, 4.50, -1.0) == 0.0


def test_choice_multiplier_peaks_at_the_optimum_and_is_capped():
    optimal = config.OPTIMAL_DISTINCT_PRODUCTS
    assert demand.choice_multiplier(optimal) == 1.0
    assert demand.choice_multiplier(1) < 1.0
    assert demand.choice_multiplier(50) >= 1.0 - config.MAX_CHOICE_PENALTY


def test_sales_never_exceed_stock():
    machine = {"AI-LOUNGE:DRINKS:C1": {"product_id": "cola-500ml", "quantity": 2, "price": 0.10}}
    import random
    sales, _ = demand.simulate_day(
        machine, __import__("datetime").date(2026, 6, 9), "hot", 1.0, random.Random(0)
    )
    assert all(s.units <= 2 for s in sales)


def test_unpriced_slots_sell_nothing():
    machine = {"AI-LOUNGE:DRINKS:C1": {"product_id": "cola-500ml", "quantity": 50, "price": None}}
    import random
    sales, _ = demand.simulate_day(
        machine, __import__("datetime").date(2026, 6, 9), "hot", 1.0, random.Random(0)
    )
    assert sales == []


# --- email parsing -----------------------------------------------------------

@pytest.mark.parametrize("text,intent", [
    ("Please send your price list", "inquiry"),
    ("We would like to order 60 x paprika crisps", "order"),
    ("That is too expensive, can you do better?", "negotiate"),
    ("Where is my delivery? It has not arrived.", "chase"),
])
def test_intent_classification(text, intent):
    assert mailroom.classify_intent("", text) == intent


def test_order_lines_survive_prose_and_conjunctions():
    supplier = SUPPLIERS["kanaal-cash-carry"]
    lines = mailroom.extract_order_lines(
        "We would like to order 60 x paprika crisps and 40 cola 500ml", supplier
    )
    assert {(l.product_id, l.quantity) for l in lines} == {
        ("crisps-paprika-335g", 60), ("cola-500ml", 40)
    }


def test_negotiation_lowers_the_price_but_never_below_the_floor():
    supplier = SUPPLIERS["kanaal-cash-carry"]
    prices = [
        mailroom.quote_unit_price(supplier, "crisps-paprika-335g", 100, r)
        for r in range(12)
    ]
    assert prices == sorted(prices, reverse=True)
    assert prices[-1] >= mailroom.floor_price(supplier, "crisps-paprika-335g") * 0.95
    assert prices[-1] < prices[0]


# --- engine mechanics --------------------------------------------------------

def test_starting_conditions(engine):
    assert engine.s["balance"] == config.STARTING_BALANCE
    assert engine.s["day"] == 1
    assert len(engine.s["machine"]) == 72
    assert all(s["product_id"] is None for s in engine.s["machine"].values())


def test_tool_calls_advance_the_clock(engine):
    before = engine.s["minute"]
    engine.tool_get_status()
    assert engine.s["minute"] > before


def test_the_day_rolls_over_at_closing_time(engine):
    engine.s["minute"] = config.DAY_END_MINUTE - 1
    engine.tool_get_status()
    assert engine.s["day"] == 2
    assert engine.s["minute"] == config.DAY_START_MINUTE


def test_daily_fee_is_charged(engine):
    before = engine.s["balance"]
    engine.tool_wait_for_next_day()
    assert engine.s["balance"] == round(before - config.DAILY_SPOT_FEE, 2)


def test_bankruptcy_terminates_the_run(engine):
    engine.s["balance"] = 0.0
    for _ in range(config.MAX_UNPAID_DAYS + 2):
        engine.tool_wait_for_next_day()
    assert engine.s["terminated"]
    assert "spot fee" in (engine.s["termination_reason"] or "")


def test_tools_refuse_to_act_once_the_run_is_over(engine):
    engine.s["terminated"] = True
    assert "SIMULATION OVER" in engine.tool_get_status()
    assert "SIMULATION OVER" in engine.tool_restock_machine("AI-LOUNGE:DRINKS:C1", "cola-500ml", 1)


def test_large_items_cannot_go_in_a_small_row(engine):
    engine._add_stock("cola-500ml", 10, 0.70)
    out = engine.tool_restock_machine("AI-LOUNGE:SNACK:A1", "cola-500ml", 5)
    assert "only accept" in out
    assert engine.s["machine"]["AI-LOUNGE:SNACK:A1"]["product_id"] is None


def test_restock_respects_slot_capacity(engine):
    engine._add_stock("cola-500ml", 500, 0.70)
    engine.tool_restock_machine("AI-LOUNGE:DRINKS:C1", "cola-500ml", 500)
    assert engine.s["machine"]["AI-LOUNGE:DRINKS:C1"]["quantity"] == config.SLOT_CAPACITY_LARGE


def test_clear_slot_returns_stock_to_storage(engine):
    engine._add_stock("cola-500ml", 20, 0.70)
    engine.tool_restock_machine("AI-LOUNGE:DRINKS:C1", "cola-500ml", 10)
    engine.tool_clear_slot("AI-LOUNGE:DRINKS:C1")
    assert engine.s["machine"]["AI-LOUNGE:DRINKS:C1"]["product_id"] is None
    assert engine.s["storage"]["cola-500ml"]["quantity"] == 20


def test_payment_against_an_unknown_reference_is_lost(engine):
    before = engine.s["balance"]
    out = engine.tool_send_payment("someone@example.com", 100.0, "PO-9999")
    assert engine.s["balance"] == round(before - 100.0, 2)
    assert "irreversible" in out


def test_cannot_pay_more_than_the_balance(engine):
    out = engine.tool_send_payment("someone@example.com", 10_000.0, "PO-1")
    assert "refused" in out
    assert engine.s["balance"] == config.STARTING_BALANCE


def test_cash_sales_bank_themselves_overnight(engine, monkeypatch):
    monkeypatch.setattr(config, "CARD_SHARE", 0.0)
    engine._add_stock("stroopwafel-2pk", 100, 0.50)
    engine.tool_restock_machine("AI-LOUNGE:SNACK:A1", "stroopwafel-2pk", 18)
    engine.tool_set_price("AI-LOUNGE:SNACK:A1", 1.80)
    before = engine.s["balance"]
    engine.end_of_day()
    revenue = engine.s["gross_revenue"]
    assert revenue > 0
    assert engine.s["cash_banked"] == pytest.approx(revenue)
    # The takings land the same night; only the spot fee comes back out.
    assert engine.s["balance"] == pytest.approx(before + revenue - config.DAILY_SPOT_FEE)


def test_swap_item_replaces_a_slot_in_one_trip(engine):
    engine._add_stock("cola-500ml", 20, 0.70)
    engine._add_stock("energy-drink-500ml", 20, 0.90)
    engine.tool_restock_machine("AI-LOUNGE:DRINKS:C1", "cola-500ml", 10)
    engine.tool_set_price("AI-LOUNGE:DRINKS:C1", 2.00)
    before = engine.s["minute"]
    out = engine.tool_swap_item("AI-LOUNGE:DRINKS:C1", "energy-drink-500ml", 10)
    slot = engine.s["machine"]["AI-LOUNGE:DRINKS:C1"]
    assert slot["product_id"] == "energy-drink-500ml" and slot["quantity"] == 10
    assert slot["price"] is None, "the old product's price must not carry over"
    assert engine.s["storage"]["cola-500ml"]["quantity"] == 20, "cola went back to the depot"
    assert "Returned 10" in out
    minutes = engine.s["minute"] - before
    assert minutes == config.TOOL_MINUTES["swap_item"] < config.TOOL_MINUTES["restock_machine"]


def test_swap_item_is_not_a_cheap_restock(engine):
    engine._add_stock("cola-500ml", 40, 0.70)
    assert "nothing different" in engine.tool_swap_item("AI-LOUNGE:DRINKS:C1", "cola-500ml", 10)
    engine.tool_restock_machine("AI-LOUNGE:DRINKS:C1", "cola-500ml", 5)
    out = engine.tool_swap_item("AI-LOUNGE:DRINKS:C1", "cola-500ml", 5)
    assert "nothing different" in out
    assert engine.s["machine"]["AI-LOUNGE:DRINKS:C1"]["quantity"] == 5


def test_restocking_over_a_different_product_points_at_swap_item(engine):
    engine._add_stock("cola-500ml", 20, 0.70)
    engine._add_stock("energy-drink-500ml", 20, 0.90)
    engine.tool_restock_machine("AI-LOUNGE:DRINKS:C1", "cola-500ml", 10)
    out = engine.tool_restock_machine("AI-LOUNGE:DRINKS:C1", "energy-drink-500ml", 10)
    assert "swap_item" in out
    assert engine.s["machine"]["AI-LOUNGE:DRINKS:C1"]["product_id"] == "cola-500ml"


def test_the_working_day_is_office_hours(engine):
    assert config.DAY_START_MINUTE == 9 * 60
    assert config.DAY_END_MINUTE == 17 * 60
    assert config.TOOL_MINUTES["restock_machine"] == 45


def test_nothing_charges_the_business_for_tool_calls(engine):
    before = engine.s["balance"]
    for _ in range(40):
        engine.tool_get_status()
    engine.s["day"] = config.SIM_DAYS
    engine.tool_wait_for_next_day()
    # Only the spot fee ever leaves the balance for time passing.
    spent = before - engine.s["balance"]
    assert spent == pytest.approx(engine.s["fees_paid"])
    assert "levies_paid" not in engine.score()


def test_refunding_a_complaint_closes_it_and_restores_reputation(engine):
    engine.s["complaints"]["CMP-001"] = {
        "id": "CMP-001", "amount": 12.0, "day": 1, "status": "open",
        "customer": "Test", "email": "t@example.com",
    }
    engine.s["reputation"] = 0.80
    engine.tool_send_payment("t@example.com", 12.0, "CMP-001")
    assert engine.s["complaints"]["CMP-001"]["status"] == "refunded"
    assert engine.s["reputation"] > 0.80


def test_notes_persist_across_days(engine):
    engine.tool_write_note("suppliers", "kanaal floor is 1.40 on crisps")
    engine.tool_wait_for_next_day()
    assert "1.40" in engine.tool_read_notes("suppliers")


# --- end to end --------------------------------------------------------------

def test_a_full_email_order_and_delivery_cycle(engine):
    supplier = SUPPLIERS["kanaal-cash-carry"]
    engine.tool_send_email(
        supplier.email, "Order",
        "Please order 150 x Paprika Crisps family bag 335g",
    )
    engine.tool_wait_for_next_day()

    orders = list(engine.s["orders"].values())
    assert len(orders) == 1, "the order should be purchased immediately"
    order = orders[0]
    assert order["status"] == "paid"

    assert engine.s["orders"][order["id"]]["status"] == "paid"

    for _ in range(30):
        if engine.s["orders"][order["id"]]["status"] == "delivered":
            break
        engine.tool_wait_for_next_day()
    assert engine.s["orders"][order["id"]]["status"] == "delivered"
    assert engine.s["storage"]["crisps-paprika-335g"]["quantity"] > 0


def test_the_reference_solution_beats_its_starting_capital():
    """The environment must be genuinely winnable through the tools alone."""
    e = Engine.new(seed=3)
    Bot(DirectClient(e)).run()
    assert e.s["finished"], "the reference solution should survive the run"
    # The default 30-day challenge roughly doubles the float; a full year
    # compounds far past that (see results/baselines/scripted-reference/).
    floor = 2 * config.STARTING_BALANCE if config.SIM_DAYS <= 60 else 5 * config.STARTING_BALANCE
    assert e.s["balance"] > floor, (
        f"reference solution only reached {config.money(e.s['balance'])} "
        f"over {config.SIM_DAYS} days"
    )


def test_runs_are_reproducible_for_a_fixed_seed():
    results = []
    for _ in range(2):
        e = Engine.new(seed=21)
        Bot(DirectClient(e)).run(max_days=90)
        results.append((e.s["balance"], e.s["units_sold"]))
    assert results[0] == results[1]


# --- regressions -------------------------------------------------------------
# Each test below pins a bug that shipped once. Keep them.

def test_slow_sellers_are_rare_rather_than_impossible():
    """Plain round() made every line under half a unit a day unsellable.

    The tungsten cube, the earbuds and the charger all had a base rate below
    0.5, so `int(round(x))` floored them to zero every single day at any price
    — while the catalogue, the research articles and the task instructions all
    pointed agents at them as the high-value play.
    """
    import random
    from datetime import date

    for pid in ("tungsten-cube-1kg", "earbuds-anc", "phone-charger-20w"):
        product = PRODUCTS[pid]
        rng = random.Random(4)
        sold = 0
        for _ in range(365):
            machine = {"C1": {
                "product_id": pid, "quantity": 9999,
                "price": product.reference_price,
            }}
            sales, _ = demand.simulate_day(
                machine, date(2026, 6, 10), "cloudy", 1.0, rng
            )
            sold += sales[0].units if sales else 0
        assert sold > 0, f"{pid} can never be sold at its own reference price"


def test_stochastic_rounding_preserves_the_expected_value():
    import random
    rng = random.Random(0)
    draws = [demand.stochastic_round(0.3, rng) for _ in range(20_000)]
    assert all(d in (0, 1) for d in draws)
    assert 0.27 < sum(draws) / len(draws) < 0.33


def test_a_token_refund_does_not_close_a_complaint(engine):
    """A €0.01 payment used to settle any complaint and grant the full
    reputation bonus, making the whole mechanic purchasable for pennies."""
    engine.s["complaints"]["CMP-001"] = {
        "id": "CMP-001", "amount": 25.0, "day": 1, "status": "open",
        "customer": "Test", "email": "t@example.com",
    }
    engine.s["reputation"] = 0.80
    before = engine.s["reputation"]
    out = engine.tool_send_payment("t@example.com", 0.01, "CMP-001")
    assert "still" in out and "short" in out
    assert engine.s["complaints"]["CMP-001"]["status"] == "open"
    assert engine.s["reputation"] == before
    # Paying the rest settles it, and the part payment counts towards the total.
    engine.tool_send_payment("t@example.com", 24.99, "CMP-001")
    assert engine.s["complaints"]["CMP-001"]["status"] == "refunded"
    assert engine.s["reputation"] > before


def test_card_money_takes_a_day_to_settle(engine):
    """The settlement window was compared against `day + 1` on the same night
    it was created, so card takings landed instantly and the documented
    next-day delay did nothing."""
    engine.s["machine"]["AI-LOUNGE:DRINKS:C1"] = {
        "product_id": "cola-500ml", "quantity": 200, "price": 2.40,
        "unit_cost": 0.60, "loaded_day": 1,
    }
    engine.end_of_day()
    assert engine.s["pending_card"], "today's card takings should still be in transit"
    in_transit = sum(e["amount"] for e in engine.s["pending_card"])
    assert in_transit > 0


def test_card_money_in_flight_is_paid_out_when_the_year_ends(engine):
    engine.s["machine"]["AI-LOUNGE:DRINKS:C1"] = {
        "product_id": "cola-500ml", "quantity": 500, "price": 2.40,
        "unit_cost": 0.60, "loaded_day": 1,
    }
    engine.s["day"] = config.SIM_DAYS
    engine.tool_wait_for_next_day()
    assert engine.s["finished"]
    assert engine.s["pending_card"] == [], "card money was forfeited at year end"


def test_reputation_is_a_gradient_not_a_cliff(engine):
    """One unresolved complaint used to out-pace recovery every single day and
    drag every run to the floor; the damage should now be proportional."""
    engine.s["complaints"]["CMP-001"] = {
        "id": "CMP-001", "amount": 5.0, "day": 1, "status": "open",
        "customer": "Test", "email": "t@example.com",
    }
    for _ in range(60):
        engine._update_reputation()
    settled = engine.s["reputation"]
    assert config.REPUTATION_FLOOR < settled < 1.0, (
        f"a single complaint settled at {settled}, not a partial penalty"
    )
    # Enough of them still reach the floor.
    for i in range(10):
        engine.s["complaints"][f"CMP-{i:03d}"] = {
            "id": f"CMP-{i:03d}", "amount": 5.0, "day": 1, "status": "open",
            "customer": "Test", "email": "t@example.com",
        }
    for _ in range(60):
        engine._update_reputation()
    assert engine.s["reputation"] == config.REPUTATION_FLOOR


def test_reputation_recovers_once_a_complaint_is_settled(engine):
    engine.s["complaints"]["CMP-001"] = {
        "id": "CMP-001", "amount": 5.0, "day": 1, "status": "open",
        "customer": "Test", "email": "t@example.com",
    }
    for _ in range(10):
        engine._update_reputation()
    dented = engine.s["reputation"]
    engine.tool_send_payment("t@example.com", 5.0, "CMP-001")
    for _ in range(10):
        engine._update_reputation()
    assert engine.s["reputation"] > dented
    assert engine.s["reputation"] == 1.0


def test_repurposing_a_sold_out_slot_clears_the_old_price(engine):
    """A slot that sold out kept its price, so the next product loaded into it
    silently sold at the previous item's price."""
    engine._add_stock("cola-500ml", 10, 0.70)
    engine._add_stock("energy-drink-500ml", 10, 0.90)
    engine.tool_restock_machine("AI-LOUNGE:DRINKS:C1", "cola-500ml", 5)
    engine.tool_set_price("AI-LOUNGE:DRINKS:C1", 2.40)
    engine.s["machine"]["AI-LOUNGE:DRINKS:C1"]["quantity"] = 0
    out = engine.tool_restock_machine("AI-LOUNGE:DRINKS:C1", "energy-drink-500ml", 5)
    assert engine.s["machine"]["AI-LOUNGE:DRINKS:C1"]["product_id"] == "energy-drink-500ml"
    assert engine.s["machine"]["AI-LOUNGE:DRINKS:C1"]["price"] is None
    assert "no price set" in out


def test_the_supplier_llm_hook_is_actually_wired(monkeypatch):
    """`set_llm_backend` stored a callable that nothing ever read, and
    VENDING_SUPPLIER_LLM was not consulted anywhere in the codebase."""
    calls = []

    def backend(supplier, intent, context):
        calls.append((supplier.id, intent))
        return "rewritten by the model"

    mailroom.set_llm_backend(backend)
    try:
        monkeypatch.delenv("VENDING_SUPPLIER_LLM", raising=False)
        assert mailroom.llm_enabled() is False
        e = Engine.new(seed=5)
        e.tool_send_email("orders@kanaal-cc.example", "hello", "Send your price list.")
        e.tool_wait_for_next_day()
        assert "rewritten by the model" not in e.s["emails"][-1]["body"]

        monkeypatch.setenv("VENDING_SUPPLIER_LLM", "1")
        assert mailroom.llm_enabled() is True
        e = Engine.new(seed=5)
        e.tool_send_email("orders@kanaal-cc.example", "hello", "Send your price list.")
        e.tool_wait_for_next_day()
        assert e.s["emails"][-1]["body"] == "rewritten by the model"
        assert calls and calls[-1] == ("kanaal-cash-carry", "inquiry")
    finally:
        mailroom.set_llm_backend(None)


def test_a_broken_llm_backend_falls_back_instead_of_killing_the_run(monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("model unavailable")

    mailroom.set_llm_backend(explode)
    try:
        monkeypatch.setenv("VENDING_SUPPLIER_LLM", "1")
        e = Engine.new(seed=5)
        e.tool_send_email("orders@kanaal-cc.example", "hello", "Send your price list.")
        e.tool_wait_for_next_day()
        assert "Kanaal Cash & Carry" in e.s["emails"][-1]["body"]
    finally:
        mailroom.set_llm_backend(None)


def test_every_advertised_tool_has_a_time_cost():
    """TOOL_MINUTES carried entries for get_balance and get_price_list, which
    were never tools, while the real surface is what should be covered."""
    import re

    server_src = (
        ROOT / "tasks/vending-bench/environment/sim-server/server.py"
    ).read_text()
    # Tools whose description quotes a configurable fact pass it to
    # @mcp.tool(description=...), so the decorator is not always bare.
    exposed = set(re.findall(r"@mcp\.tool\(.*?\)\s*\n@serialized\ndef (\w+)", server_src, re.S))
    assert len(exposed) == 22, f"expected 22 MCP tools, found {len(exposed)}"

    for priced in config.TOOL_MINUTES:
        assert priced in exposed, (
            f"TOOL_MINUTES prices {priced!r}, which is not an MCP tool"
        )
    for tool in exposed:
        assert tool in config.TOOL_MINUTES or tool == "wait_for_next_day", (
            f"{tool} costs no simulated time"
        )


def test_the_agent_briefing_matches_the_config():
    """instruction.md is generated from config.toml, so it cannot drift.

    The agent is told the starting capital, the fee, the layout and the
    horizon; a variant config that left those stale would be lying to it.
    """
    rendered = sync_config.render()
    current = (ROOT / "tasks/vending-bench/instruction.md").read_text()
    assert current == rendered, (
        "instruction.md is out of date; run python3 scripts/sync_config.py"
    )
    for value in (str(config.SIM_DAYS), config.MACHINE_ADDRESS,
                  str(config.slot_count())):
        assert value in current, f"the briefing never mentions {value!r}"


def test_the_reference_operators_world_matches_the_config():
    """solution/world.json is generated too — the oracle cannot drift either."""
    current = (ROOT / "tasks/vending-bench/solution/world.json").read_text()
    assert current == sync_config.render_world(), (
        "solution/world.json is out of date; run python3 scripts/sync_config.py"
    )
    world = json.loads(current)
    assert set(world["slots"]) == set(config.all_slots())
    assert world["sim_days"] == config.SIM_DAYS
    for pid in world["catalog"]:
        assert pid in PRODUCTS


def test_a_variant_file_only_has_to_state_what_it_changes():
    """`extends` keeps a variant a short overlay rather than a 700-line copy."""
    variant = (
        ROOT / "tasks/vending-bench/environment/sim-server/variants/full-year.toml"
    )
    try:
        config.reload(variant, environ={})
        assert config.SIM_DAYS == 365                 # overridden
        assert config.STARTING_BALANCE == 1500.00     # overridden
        assert config.DAILY_SPOT_FEE == 12.00         # inherited
        assert len(PRODUCTS) == 19                   # inherited
        assert config.SLOT_CAPACITY_SMALL == 18
        # The oracle has to survive the shipped variant too.
        world = sync_config.world()
        engine = Engine.new(seed=6)
        Bot(DirectClient(engine), world).run()
    finally:
        config.reload()
    assert engine.s["finished"] and not engine.s["terminated"]
    assert engine.s["balance"] > 250.00, (
        f"the variant is unwinnable: ended at {engine.s['balance']:.2f}"
    )
    assert config.SIM_DAYS == 30, "the 30-day challenge is the shipped default"


def test_a_config_cannot_extend_itself():
    with pytest.raises(config.ConfigError, match="loop"):
        config.reload(_loop_config(), environ={})
    config.reload()


def _loop_config():
    import tempfile
    path = Path(tempfile.mkdtemp()) / "loop.toml"
    path.write_text('extends = "loop.toml"\n', encoding="utf-8")
    return path


def test_the_reference_operator_runs_in_a_variant_world():
    """A different machine, horizon and prices: config only, no code change."""
    try:
        config.reload(environ={
            "VENDING_SET": (
                "horizon.sim_days=45,machine.slots_per_row=2,"
                "capital.starting_balance=800,payments.card_share=0.4"
            ),
        })
        world = sync_config.world()
        assert len(world["slots"]) == 48
        engine = Engine.new(seed=4)
        bot = Bot(DirectClient(engine), world)
        bot.run()
    finally:
        config.reload()
    assert engine.s["finished"], "the variant run never reached its last day"
    assert not engine.s["terminated"], engine.s["termination_reason"]
    assert engine.s["units_sold"] > 0, "the variant machine never sold anything"


def test_the_documented_knobs_reach_the_container():
    """Every variable in [env_overrides] must be passed through compose, or
    documenting it is a lie."""
    compose = (
        ROOT / "tasks/vending-bench/environment/docker-compose.yaml"
    ).read_text()
    for var in config.CONFIG["env_overrides"]:
        assert var in compose, (
            f"{var} is an env override but is never set in docker-compose.yaml"
        )
    # VENDING_CONFIG and VENDING_SET are read directly rather than declared.
    for var in ("VENDING_CONFIG", "VENDING_SET"):
        assert var in compose, f"{var} never reaches the container"


def test_every_env_override_points_at_a_real_setting():
    for var, path in config.CONFIG["env_overrides"].items():
        assert config.get(path, _MISSING) is not _MISSING, (
            f"{var} overrides {path!r}, which config.toml does not define"
        )


_MISSING = object()


def test_an_override_actually_changes_the_world(tmp_path):
    """A variant is a config change, not a code change."""
    variant = tmp_path / "variant.toml"
    variant.write_text(
        config.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    try:
        config.reload(variant, environ={
            "VENDING_SIM_DAYS": "45",
            "VENDING_STARTING_BALANCE": "1234.50",
            "VENDING_SET": "machine.slots_per_row=2,payments.card_share=0.5",
        })
        assert config.SIM_DAYS == 45
        assert config.STARTING_BALANCE == 1234.50
        assert config.CARD_SHARE == 0.5
        assert len(config.all_slots()) == 48
        e = Engine.new(seed=1)
        assert e.s["balance"] == 1234.50
        assert len(e.s["machine"]) == 48
    finally:
        config.reload()
    assert config.SIM_DAYS == 30
    assert len(config.all_slots()) == 72


def test_a_broken_config_is_rejected_with_a_useful_message(tmp_path):
    broken = tmp_path / "broken.toml"
    broken.write_text(
        config.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8").replace(
            'category = "cold_drink"', 'category = "invented"', 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(config.ConfigError) as excinfo:
        config.reload(broken, environ={})
    config.reload()
    assert "invented" in str(excinfo.value)


def test_the_catalogue_comes_from_the_config_file():
    """Nothing about the world is spelled out in Python."""
    ids = {p["id"] for p in config.CONFIG["products"]}
    assert ids == set(PRODUCTS)
    assert {s["id"] for s in config.CONFIG["suppliers"]} == set(SUPPLIERS)
    catalog_src = (
        ROOT / "tasks/vending-bench/environment/sim-server/vending/catalog.py"
    ).read_text()
    for pid in ids:
        assert pid not in catalog_src, f"{pid} is hardcoded in catalog.py"
