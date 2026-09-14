"""Supplier correspondence.

Generating supplier replies with an LLM would make the eval depend on a second
model and a second bill, and would make runs non-reproducible.
This module instead classifies the agent's email into an intent, pulls out any
order lines, and answers from the supplier's persona with a deterministic
negotiation curve. Prices genuinely move when the agent pushes, adversarial
suppliers genuinely try it on, and the same seed replays exactly.

Every regex, threshold, persona voice and concession pace used here is read
from config.toml ([mail], [negotiation] and [personas]).

Set VENDING_SUPPLIER_LLM=1 and provide a callable through
`set_llm_backend()` to swap the templated replies for a real model while
keeping the same order and pricing mechanics.
"""

from __future__ import annotations

import difflib
import functools
import math
import os
import re
from dataclasses import dataclass

from . import config
from .catalog import PRODUCTS, Supplier

# --- Intent classification ---------------------------------------------------

# Intent patterns, their ranking and every regex below come from [mail] in
# config.toml; nothing here is spelled out in code.


def _intent_patterns() -> list[tuple[str, tuple[str, ...]]]:
    return config.INTENT_PATTERNS


def classify_intent(subject: str, body: str) -> str:
    text = f"{subject}\n{body}".lower()
    scores: dict[str, int] = {}
    for intent, patterns in _intent_patterns():
        hits = sum(1 for p in patterns if re.search(p, text))
        if hits:
            scores[intent] = hits
    if not scores:
        return "order" if _has_order_lines(text) else "other"
    # An explicit order beats a negotiation mention in the same mail; the
    # agent is telling us to ship, and any price talk is context.
    if "order" in scores and _has_order_lines(text):
        return "order"
    # You cannot order without saying what and how many. "Please send your
    # price list" trips the order verbs but is plainly an enquiry, so an
    # order with no quantities loses to any other intent present.
    if "order" in scores and len(scores) > 1:
        scores.pop("order")
    return max(
        scores.items(),
        key=lambda kv: (kv[1], config.INTENT_RANK.get(kv[0], 0)),
    )[0]


@functools.lru_cache(maxsize=64)
def _rx(pattern: str) -> re.Pattern:
    """Compile a configured regex once, keyed on the pattern itself.

    Cached on the pattern rather than at import time so a config.reload() with
    different regexes takes effect without restarting the process.
    """
    return re.compile(pattern, re.I)


def _has_order_lines(text: str) -> bool:
    return bool(_extract_pairs(text))


def _segments(text: str) -> list[str]:
    """Split an email body into candidate order lines.

    Order lines are written one per line, or run together with "and"/commas.
    Splitting first keeps a greedy product name from swallowing the next line,
    and the trailing-price pattern strips "60 x cola at EUR 0.80 each" back to
    the part that names a product.
    """
    out = []
    for raw in _rx(config.SEGMENT_SPLIT_PATTERN).split(text):
        seg = _rx(config.BULLET_PATTERN).sub("", raw or "").strip()
        seg = _rx(config.TRAILING_PRICE_PATTERN).sub("", seg).strip(" .\t")
        if seg:
            out.append(seg)
    return out


def _extract_pairs(text: str) -> list[tuple[int, str]]:
    pairs: list[tuple[int, str]] = []
    for seg in _segments(text):
        for pattern in config.QTY_PATTERNS:
            m = re.search(pattern, seg, re.I)
            if not m:
                continue
            try:
                qty = int(m.group("qty"))
            except (ValueError, IndexError):
                continue
            name = (m.group("name") or "").strip()
            if (qty <= 0 or qty > config.MATCH_MAX_QUANTITY
                    or len(name) < config.MATCH_MIN_NAME_LENGTH):
                continue
            pairs.append((qty, name))
            break
    return pairs


# --- Product matching --------------------------------------------------------

def _tokens(text: str) -> list[str]:
    """Words of a phrase, minus the [mail].stop_words noise."""
    return [
        t for t in re.findall(r"[a-z0-9]+", text.lower())
        if t not in config.STOP_WORDS
    ]


def match_product(phrase: str, candidate_ids: tuple[str, ...] | list[str]) -> str | None:
    """Best-effort fuzzy match of free text onto one of the supplier's SKUs."""
    phrase_tokens = set(_tokens(phrase))
    if not phrase_tokens:
        return None
    best: tuple[float, str] | None = None
    for pid in candidate_ids:
        product = PRODUCTS.get(pid)
        if product is None:
            continue
        score = 0.0
        for term in product.search_terms:
            term_tokens = set(_tokens(term))
            if not term_tokens:
                continue
            overlap = len(phrase_tokens & term_tokens) / len(term_tokens)
            ratio = difflib.SequenceMatcher(
                None, " ".join(sorted(phrase_tokens)), " ".join(sorted(term_tokens))
            ).ratio()
            score = max(
                score,
                overlap * config.MATCH_OVERLAP_WEIGHT
                + ratio * config.MATCH_RATIO_WEIGHT,
            )
        if best is None or score > best[0]:
            best = (score, pid)
    if best and best[0] >= config.MATCH_THRESHOLD:
        return best[1]
    return None


@dataclass
class OrderLine:
    product_id: str
    quantity: int
    unit_price: float = 0.0

    @property
    def total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


def extract_order_lines(text: str, supplier: Supplier) -> list[OrderLine]:
    """Pull quantity/product pairs out of the agent's email."""
    found: dict[str, int] = {}
    for qty, name in _extract_pairs(text):
        pid = match_product(name, supplier.product_ids)
        if pid:
            found[pid] = found.get(pid, 0) + qty
    return [OrderLine(pid, qty) for pid, qty in found.items()]


# --- Pricing and negotiation -------------------------------------------------

def list_price(supplier: Supplier, product_id: str) -> float:
    return round(PRODUCTS[product_id].true_cost * supplier.markup, 2)


def floor_price(supplier: Supplier, product_id: str) -> float:
    return round(list_price(supplier, product_id) * supplier.floor, 2)


def quote_unit_price(
    supplier: Supplier, product_id: str, quantity: int, rounds: int
) -> float:
    """Price after `rounds` of pushback at this order size.

    Concession approaches the supplier's floor asymptotically, so persistence
    pays but never for free. Volume buys a little extra on top.
    """
    top = list_price(supplier, product_id)
    bottom = floor_price(supplier, product_id)
    pace = config.PERSONA_PACE.get(
        supplier.persona, config.PERSONA_PACE.get("default", 0.55)
    )
    progress = 1.0 - math.exp(-pace * max(0, rounds))
    volume_bonus = min(
        config.VOLUME_BONUS_CAP,
        config.VOLUME_BONUS_COEFFICIENT
        * math.log1p(max(0, quantity) / config.VOLUME_REFERENCE_QUANTITY),
    )
    progress = max(0.0, min(1.0, progress + volume_bonus))
    return round(top - (top - bottom) * progress, 2)


# --- Reply composition -------------------------------------------------------

_LLM_BACKEND = None


def set_llm_backend(fn) -> None:
    """Install a callable(supplier, intent, context) -> str for LLM replies.

    The backend only rewrites the *prose* of a reply. Prices, order lines,
    invoice numbers and the concession curve are computed deterministically
    either way and are handed to the backend in `context`, so switching a model
    on cannot change what a run costs or what ships — only how it reads.
    """
    global _LLM_BACKEND
    _LLM_BACKEND = fn


def llm_enabled() -> bool:
    """True when a backend is installed and VENDING_SUPPLIER_LLM asks for it."""
    setting = os.environ.get("VENDING_SUPPLIER_LLM", config.SUPPLIER_LLM)
    return _LLM_BACKEND is not None and setting not in ("", "0", "false", "no")


def compose(supplier: Supplier, intent: str, fallback: str, **context) -> str:
    """Return the templated reply, or the backend's rewrite of it.

    Any failure in the backend falls back to the deterministic text rather than
    breaking a run that may be thousands of tool calls deep.
    """
    if not llm_enabled():
        return fallback
    try:
        out = _LLM_BACKEND(supplier, intent, {"draft": fallback, **context})
    except Exception:
        return fallback
    return out if isinstance(out, str) and out.strip() else fallback


def _money(x: float) -> str:
    """Format an amount in the configured currency."""
    return config.money(x)


def signoff(supplier: Supplier) -> str:
    """The persona's sign-off, with the supplier's own contact name."""
    template = config.persona(supplier.persona, "signoff", "{contact}\n{company}")
    return template.format(contact=supplier.contact, company=supplier.name)


def catalog_reply(supplier: Supplier, rounds_by_product: dict[str, int]) -> str:
    lines = [f"Thanks for reaching out to {supplier.name}.", "", "Current list prices:"]
    for pid in supplier.product_ids:
        product = PRODUCTS[pid]
        rounds = rounds_by_product.get(pid, 0)
        price = quote_unit_price(supplier, pid, 0, rounds)
        lines.append(f"  - {product.name} — {_money(price)} per unit")
    lines += [
        "",
        f"Minimum order value is {_money(supplier.min_order_value)}. "
        f"Typical delivery {supplier.delivery_days[0]}-{supplier.delivery_days[1]} "
        "working days to your depot.",
        "",
        "To order, reply with the quantities you want and we will send a "
        "proforma invoice. Payment is due before we ship.",
        "",
        signoff(supplier),
    ]
    preamble = config.persona(supplier.persona, "catalog_preamble", "")
    if preamble:
        lines.insert(1, preamble)
    return "\n".join(lines)


def mentioned_products(text: str, supplier: Supplier) -> list[str]:
    """Products referred to without a quantity ("best price on stroopwafels?")."""
    hits: list[str] = []
    for seg in _rx(config.MENTION_SPLIT_PATTERN).split(text):
        pid = match_product(seg, supplier.product_ids)
        if pid and pid not in hits:
            hits.append(pid)
    return hits


def quote_reply(
    supplier: Supplier, lines: list[OrderLine], rounds_by_product: dict[str, int]
) -> str:
    out = [f"Thanks for the enquiry. Here is what we can do today:", ""]
    total = 0.0
    for line in lines:
        product = PRODUCTS[line.product_id]
        rounds = rounds_by_product.get(line.product_id, 0)
        unit = quote_unit_price(supplier, line.product_id, line.quantity, rounds)
        total += unit * line.quantity
        out.append(
            f"  {line.quantity} x {product.name} @ {_money(unit)} "
            f"= {_money(unit * line.quantity)}"
        )
    out += ["", f"Indicative total: {_money(total)}"]
    if total < supplier.min_order_value:
        out.append(
            f"Note: our minimum order value is {_money(supplier.min_order_value)}. "
            "Please top the order up before we can process it."
        )
    out += [
        "",
        "This is a quote, not an order. Reply confirming the quantities and we "
        "will issue a proforma invoice.",
        "",
        signoff(supplier),
    ]
    return "\n".join(out)


def negotiate_reply(
    supplier: Supplier,
    product_ids: list[str],
    rounds_by_product: dict[str, int],
    quantities: dict[str, int],
    at_floor: bool,
) -> str:
    openers = config.persona(supplier.persona, "negotiation_openers") or [""]
    round_index = max((rounds_by_product.get(p, 0) for p in product_ids), default=1)
    out = [openers[min(round_index - 1, len(openers) - 1)] if round_index else openers[0], ""]
    for pid in product_ids:
        product = PRODUCTS[pid]
        rounds = rounds_by_product.get(pid, 0)
        qty = quantities.get(pid, 0)
        unit = quote_unit_price(supplier, pid, qty, rounds)
        out.append(f"  {product.name} — {_money(unit)} per unit")
    if at_floor:
        out += ["", config.persona(supplier.persona, "at_floor_line", "")]
    out += ["", signoff(supplier)]
    return "\n".join(out)


def proforma_reply(
    supplier: Supplier, order_id: str, lines: list[OrderLine], total: float, eta: str
) -> str:
    out = [
        f"Proforma invoice {order_id}",
        "",
    ]
    for line in lines:
        product = PRODUCTS[line.product_id]
        out.append(
            f"  {line.quantity} x {product.name} @ {_money(line.unit_price)} "
            f"= {_money(line.total)}"
        )
    out += [
        "",
        f"Total due: {_money(total)}",
        f"Payment reference: {order_id}   (quote this exactly)",
        f"Ship to: {_ship_to()}",
        f"Expected delivery: {eta} after payment clears.",
        "",
        "We ship on receipt of payment. Unpaid proformas are cancelled after "
        f"{_payment_window()} days.",
        "",
        signoff(supplier),
    ]
    return "\n".join(out)


def _ship_to() -> str:
    return config.STORAGE_ADDRESS


def _payment_window() -> int:
    return config.ORDER_PAYMENT_WINDOW_DAYS


def below_minimum_reply(supplier: Supplier, total: float) -> str:
    return "\n".join([
        f"Thanks for the order. Unfortunately it comes to {_money(total)}, and our "
        f"minimum order value is {_money(supplier.min_order_value)}.",
        "",
        "Add a few more lines and we will get it straight out to you.",
        "",
        signoff(supplier),
    ])


def unknown_items_reply(supplier: Supplier) -> str:
    preview = config.CATALOG_PREVIEW_COUNT
    names = ", ".join(PRODUCTS[p].name for p in supplier.product_ids[:preview])
    return "\n".join([
        "Thanks for your message, but I could not match what you asked for to "
        "anything in our range.",
        "",
        f"We stock: {names}"
        + (", and more." if len(supplier.product_ids) > preview else "."),
        "",
        "Please reply with quantities and product names as they appear on our "
        "price list, one per line.",
        "",
        signoff(supplier),
    ])


def chase_reply(supplier: Supplier, open_orders: list[str]) -> str:
    body = config.persona(supplier.persona, "chase_reply", "")
    refs = ", ".join(open_orders) if open_orders else "none that I can see"
    return "\n".join([
        body, "", f"Open orders on your account: {refs}.", "", signoff(supplier)
    ])


def generic_reply(supplier: Supplier) -> str:
    return "\n".join([
        f"Thanks for your message. This is {supplier.contact} at "
        f"{supplier.name}.",
        "",
        "If you would like our price list, just ask. To order, send quantities "
        "and product names, one per line, and we will issue a proforma invoice.",
        "",
        signoff(supplier),
    ])
