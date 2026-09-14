"""MCP server exposing the Vending-Bench vending business to an agent.

Every tool advances simulated time, so the agent's day is a real budget: how
long each call costs and when the day ends are set in vending/config.toml, as is
everything else about the world. State is written to disk after every call,
which is what the verifier reads to score the run.
"""

from __future__ import annotations

import json
import os
import threading
from functools import wraps
from pathlib import Path

from fastmcp import FastMCP

from vending import config
from vending.engine import Engine

# config already applied VENDING_STATE_PATH / VENDING_SEED / VENDING_RESUME and the
# rest of [env_overrides]; read the resolved values rather than the raw env.
STATE_PATH = Path(config.STATE_PATH)
SEED = config.SEED

if STATE_PATH.exists() and config.RESUME == "1":
    ENGINE = Engine.load(STATE_PATH)
    ENGINE.path = STATE_PATH
else:
    ENGINE = Engine.new(SEED, STATE_PATH)

mcp = FastMCP(config.SERVER_NAME)
LOCK = threading.RLock()


def serialized(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        with LOCK:
            return fn(*args, **kwargs)
    return run



@mcp.tool()
@serialized
def get_status() -> str:
    """Current date, time of day, money balance, cash in the machine, open
    orders, unresolved complaints and how the business is doing overall.
    Start here."""
    return ENGINE.tool_get_status()


@mcp.tool()
@serialized
def search_web(query: str) -> str:
    """Search the web for wholesalers, suppliers, product ideas and trade
    advice. This is how you find who to buy from."""
    return ENGINE.tool_search_web(query)


@mcp.tool()
@serialized
def list_emails(folder: str = "inbox", unread_only: bool = False,
                limit: int = config.EMAIL_LIST_LIMIT_DEFAULT) -> str:
    """List mail. folder is "inbox" or "sent"."""
    return ENGINE.tool_list_emails(folder, unread_only, limit)


@mcp.tool()
@serialized
def read_email(email_id: str) -> str:
    """Read one email by its id, as shown by list_emails."""
    return ENGINE.tool_read_email(email_id)


@mcp.tool()
@serialized
def send_email(to: str, subject: str, body: str) -> str:
    """Email a supplier or a customer. Replies arrive overnight and appear in
    your inbox the next morning. Order emails with quantities purchase immediately at current prices. Use order_goods for structured orders."""
    return ENGINE.tool_send_email(to, subject, body)


@mcp.tool()
@serialized
def send_payment(recipient: str, amount: float, reference: str) -> str:
    """Refund a customer. Goods are paid automatically when ordered. The reference must exactly
    match the invoice number from the proforma, or the complaint reference from
    the customer's email.

    Payments are irreversible. A payment with a reference that matches nothing
    is lost."""
    return ENGINE.tool_send_payment(recipient, amount, reference)


@mcp.tool()
@serialized
def get_storage_inventory() -> str:
    """What is sitting in the depot, with the exact product_id values you need
    for restock_machine, the quantity, and what you paid per unit."""
    return ENGINE.tool_get_storage_inventory()


@mcp.tool()
@serialized
def get_machine_inventory(machine_id: str = "") -> str:
    """What is loaded in each slot of the machine, and at what price."""
    return ENGINE.tool_get_machine_inventory(machine_id)


@mcp.tool(description=(
    "Fill a slot from the depot. Rows "
    f"{' and '.join(config.SMALL_ROWS)} take small items, rows "
    f"{' and '.join(config.LARGE_ROWS)} take large items. "
    "A slot holds one product at a time. This is the longest errand in the day."
))
@serialized
def restock_machine(slot: str, product_id: str, quantity: int) -> str:
    return ENGINE.tool_restock_machine(slot, product_id, quantity)


@mcp.tool(description=(
    "Replace or swap the product in a slot: whatever is in it goes back to the "
    "depot and the new product is loaded, in a single trip. Quicker than "
    "clear_slot plus restock_machine. The slot must currently hold a different "
    "product."
))
@serialized
def swap_item(slot: str, product_id: str, quantity: int) -> str:
    return ENGINE.tool_swap_item(slot, product_id, quantity)


@mcp.tool()
@serialized
def set_price(slot: str, price: float) -> str:
    """Set the selling price for a slot. A slot with stock but no price sells
    nothing."""
    return ENGINE.tool_set_price(slot, price)


@mcp.tool()
@serialized
def clear_slot(slot: str) -> str:
    """Empty a slot back into the depot so you can load something else."""
    return ENGINE.tool_clear_slot(slot)


@mcp.tool()
@serialized
def get_sales_report(days: int = config.SALES_REPORT_DEFAULT_DAYS,
                     by_product: bool = False, location: str = "") -> str:
    """Daily sales history, or a per-product breakdown. This is the only way to
    learn what actually sells and at what price."""
    return ENGINE.tool_get_sales_report(days, by_product, location)


@mcp.tool(description=(
    f"Save a note under a key. Notes persist for all {config.SIM_DAYS} days "
    "and survive anything being trimmed from your context. "
    "Pass an empty text to delete."
))
@serialized
def write_note(key: str, text: str) -> str:
    return ENGINE.tool_write_note(key, text)


@mcp.tool()
@serialized
def read_notes(key: str = "") -> str:
    """Read one note by key, or all of them if no key is given."""
    return ENGINE.tool_read_notes(key or None)


@mcp.tool()
@serialized
def set_reminder(text: str, in_days: int = 1) -> str:
    """Leave yourself a reminder that appears in a future morning briefing."""
    return ENGINE.tool_set_reminder(text, in_days)


@mcp.tool()
@serialized
def list_reminders() -> str:
    """Show reminders that have not fired yet."""
    return ENGINE.tool_list_reminders()


@mcp.tool(description=(
    f"Sleep. Time jumps to {config.DAY_START_MINUTE // 60:02d}:"
    f"{config.DAY_START_MINUTE % 60:02d} tomorrow. Sales happen, the spot fee "
    "is taken, deliveries arrive and supplier replies land overnight."
))
@serialized
def wait_for_next_day() -> str:
    return ENGINE.tool_wait_for_next_day()


@mcp.tool()
@serialized
def check_offers(supplier_id: str, quantity: int = 20) -> str:
    """Discover current wholesale prices at this quantity, including weekly discounts and negotiated terms.
    Prices change every seven days. Checking is optional; ordering always charges the current price."""
    return ENGINE.tool_check_offers(supplier_id, quantity)


@mcp.tool()
@serialized
def order_goods(supplier_id: str, items: dict[str, int]) -> str:
    """Buy product-id quantities now. Immediately debits current prices, even if you did not check offers.
    No preview invoice or cancellation. Receipt reports actual cost after purchase."""
    return ENGINE.tool_order_goods(supplier_id, items)


@mcp.tool()
@serialized
def run_marketing(location: str, channel: str, message: str) -> str:
    """Run a simulated campaign: prints, slack, or mail at AI-LOUNGE, AI-HOUSE, or MAIN-LOUNGE.
    Moderate use increases traffic; repeated use and cross-channel spam reduce it. Never sends real messages."""
    return ENGINE.tool_run_marketing(location, channel, message)


@mcp.tool()
@serialized
def get_marketing_report() -> str:
    """Read channel costs, limits, recent campaigns and current traffic effects at each location."""
    return ENGINE.tool_get_marketing_report()


from starlette.responses import JSONResponse


@mcp.custom_route("/verifier/finalize", methods=["POST"])
async def finalize(_request):
    # Sealing is irreversible: no tool can operate after the trajectory becomes visible.
    # There is deliberately no live /state or /score route.
    with LOCK:
        score = ENGINE.score(finalize=True)
        return JSONResponse({"score": score, "state": ENGINE.s})


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request):
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENGINE.save()
    mcp.run(transport="streamable-http", host=os.environ.get("VENDING_HOST", "0.0.0.0"), port=config.PORT)
