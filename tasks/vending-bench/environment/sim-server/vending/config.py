"""Loads config.toml and exposes it as the constants the simulation reads.

There are no economic values in this module. Every number, table, persona,
product and supplier lives in `config.toml` next to it; this file only reads
that, applies overrides, validates it, and binds the result to module-level
names so the rest of the package can keep saying `config.SIM_DAYS`.

Override order, last wins:

  1. config.toml (or the file named by VENDING_CONFIG)
  2. the named environment variables listed in the file's [env_overrides]
  3. VENDING_SET="horizon.sim_days=90,capital.starting_balance=250"

A config file may start with `extends = "<path>"` to inherit another one, so a
variant can be a short overlay rather than a 700-line copy. Tables are merged
key by key; a list (including an array of tables like [[products]]) replaces
the inherited one outright.

`reload()` re-reads everything, which is what the tests use to run a variant.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.toml")

#: The parsed, override-applied configuration. Read it directly for anything
#: that has no dedicated constant below.
CONFIG: dict[str, Any] = {}

#: Where CONFIG was actually loaded from, for logging and the /score payload.
CONFIG_PATH: Path = DEFAULT_CONFIG_PATH


class ConfigError(ValueError):
    """The configuration file is missing something or contradicts itself."""


# --- reading and overriding --------------------------------------------------

MAX_EXTENDS_DEPTH = 8


def _parse(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"no configuration file at {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc


def _merge(base: dict, overlay: dict) -> dict:
    """Overlay onto base. Tables merge; scalars and lists replace."""
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _read(path: Path, _seen: tuple[Path, ...] = ()) -> dict:
    """Parse a config file, resolving any `extends` chain beneath it."""
    path = path.resolve()
    if path in _seen:
        chain = " -> ".join(p.name for p in _seen + (path,))
        raise ConfigError(f"extends forms a loop: {chain}")
    if len(_seen) >= MAX_EXTENDS_DEPTH:
        raise ConfigError(f"extends is nested more than {MAX_EXTENDS_DEPTH} deep")

    data = _parse(path)
    parent = data.pop("extends", None)
    if parent is None:
        return data
    if not isinstance(parent, str):
        raise ConfigError(f"{path.name}: extends must be a path, got {parent!r}")
    base_path = Path(parent)
    if not base_path.is_absolute():
        base_path = path.parent / base_path
    return _merge(_read(base_path, _seen + (path,)), data)


def get(path: str, default: Any = None) -> Any:
    """Read a dotted path out of CONFIG, e.g. get("demand.noise_sd")."""
    node: Any = CONFIG
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _coerce(value: str, like: Any) -> Any:
    """Parse an override string into the type of the value it replaces."""
    if isinstance(like, bool):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(like, int) and not isinstance(like, bool):
        return int(float(value))
    if isinstance(like, float):
        return float(value)
    if isinstance(like, list):
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


def _assign(data: dict, dotted: str, value: str, source: str) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            raise ConfigError(f"{source} targets {dotted!r}, which is not a table")
    key = parts[-1]
    if key not in node:
        raise ConfigError(f"{source} targets {dotted!r}, which the config does not define")
    node[key] = _coerce(value, node[key])


def _apply_overrides(data: dict, environ: dict) -> None:
    for var, dotted in (data.get("env_overrides") or {}).items():
        if var in environ and environ[var] != "":
            _assign(data, dotted, environ[var], f"${var}")
    raw = environ.get("VENDING_SET", "")
    for clause in raw.split(","):
        clause = clause.strip()
        if not clause:
            continue
        if "=" not in clause:
            raise ConfigError(f"VENDING_SET entry {clause!r} is not path=value")
        dotted, value = clause.split("=", 1)
        _assign(data, dotted.strip(), value.strip(), "VENDING_SET")


# --- validation --------------------------------------------------------------

def _validate(c: dict) -> None:
    def need(path: str) -> Any:
        node: Any = c
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise ConfigError(f"config is missing [{path}]")
            node = node[part]
        return node

    for section in ("currency", "horizon", "capital", "machine", "clock",
                    "payments", "demand", "weather", "reputation", "complaints",
                    "orders", "negotiation", "mail", "personas", "world",
                    "search", "reports", "solution", "products", "suppliers"):
        need(section)

    if need("horizon.sim_days") < 1:
        raise ConfigError("horizon.sim_days must be at least 1")
    if need("clock.day_end_hour") <= need("clock.day_start_hour"):
        raise ConfigError("clock.day_end_hour must be after clock.day_start_hour")
    if not 0.0 <= need("payments.card_share") <= 1.0:
        raise ConfigError("payments.card_share must be between 0 and 1")

    import math
    import re
    for key in ("capital.starting_balance", "capital.daily_spot_fee"):
        value = need(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ConfigError(f"{key} must be finite and nonnegative")
    locations = need("locations")
    if not locations:
        raise ConfigError("locations must not be empty")
    for lid, loc in locations.items():
        if not re.fullmatch(r"[A-Z][A-Z-]*", lid):
            raise ConfigError("location IDs must be uppercase letters and hyphens")
        for key in ("volume", "price_sensitivity"):
            if not math.isfinite(loc[key]) or loc[key] <= 0:
                raise ConfigError(f"locations.{lid}.{key} must be positive and finite")
        if any(not math.isfinite(v) or v < 0 for v in loc["preferences"].values()):
            raise ConfigError("location preferences must be nonnegative and finite")
    offers = need("offers")
    if offers["period_days"] < 1 or not 0 <= offers["probability"] <= 1 or not offers["discounts"]:
        raise ConfigError("invalid offers period, probability or discounts")
    if any(not 0 <= d < 1 for d in offers["discounts"]):
        raise ConfigError("offer discounts must be in [0, 1)")
    marketing = need("marketing")
    if marketing["window_days"] < 1 or not 0 <= marketing["traffic_floor"] <= 1 <= marketing["traffic_cap"]:
        raise ConfigError("invalid marketing window or traffic bounds")
    if set(marketing["channels"]) != {"prints", "slack", "mail"}:
        raise ConfigError("marketing needs prints, slack and mail")
    for channel in marketing["channels"].values():
        if channel["duration_days"] < 1 or channel["duration_days"] > marketing["window_days"]:
            raise ConfigError("marketing duration must fit fatigue window")
        for field in ("cost", "bonus", "fatigue", "weekly_limit"):
            if not math.isfinite(channel[field]) or channel[field] < 0:
                raise ConfigError(f"marketing {field} must be finite and nonnegative")

    rows = need("machine.rows")
    sized = set(need("machine.small_rows")) | set(need("machine.large_rows"))
    unsized = [r for r in rows if r not in sized]
    if unsized:
        raise ConfigError(
            f"machine rows {unsized} are in neither small_rows nor large_rows"
        )

    dow = need("demand.day_of_week_multiplier")
    missing_days = [d for d in range(7) if str(d) not in dow]
    if missing_days:
        raise ConfigError(f"demand.day_of_week_multiplier is missing days {missing_days}")
    months = need("demand.month_multiplier")
    missing_months = [m for m in range(1, 13) if str(m) not in months]
    if missing_months:
        raise ConfigError(f"demand.month_multiplier is missing months {missing_months}")

    weather_types = set(need("weather.types"))
    by_month = need("weather.by_month")
    for m in range(1, 13):
        if str(m) not in by_month:
            raise ConfigError(f"weather.by_month is missing month {m}")
        table = by_month[str(m)]
        unknown = set(table) - weather_types
        if unknown:
            raise ConfigError(f"weather.by_month.{m} names unknown weather {sorted(unknown)}")
        total = sum(table.values())
        if abs(total - 1.0) > 1e-6:
            raise ConfigError(f"weather.by_month.{m} probabilities sum to {total}, not 1")
    if need("weather.fallback") not in weather_types:
        raise ConfigError("weather.fallback is not one of weather.types")

    categories = {p["category"] for p in need("products")}
    cat_table = need("weather.category_multiplier")
    for kind in weather_types:
        if kind not in cat_table:
            raise ConfigError(f"weather.category_multiplier has no row for {kind!r}")
        missing = categories - set(cat_table[kind])
        if missing:
            raise ConfigError(
                f"weather.category_multiplier.{kind} is missing categories {sorted(missing)}"
            )

    sizes = {"small", "large"}
    ids: set[str] = set()
    for product in need("products"):
        for field in ("id", "name", "size", "category", "true_cost",
                      "reference_price", "base_sales", "elasticity"):
            if field not in product:
                raise ConfigError(f"product {product.get('id', '?')} is missing {field!r}")
        if product["id"] in ids:
            raise ConfigError(f"duplicate product id {product['id']!r}")
        if product["size"] not in sizes:
            raise ConfigError(f"product {product['id']} has size {product['size']!r}")
        ids.add(product["id"])

    groups = c.get("product_groups") or {}
    for group, members in groups.items():
        unknown = [p for p in members if p not in ids]
        if unknown:
            raise ConfigError(f"product_groups.{group} names unknown products {unknown}")

    personas = need("personas")
    seen_suppliers: set[str] = set()
    for supplier in need("suppliers"):
        sid = supplier.get("id", "?")
        if sid in seen_suppliers:
            raise ConfigError(f"duplicate supplier id {sid!r}")
        seen_suppliers.add(sid)
        if supplier.get("persona") not in personas:
            raise ConfigError(
                f"supplier {sid} has persona {supplier.get('persona')!r}, "
                f"which is not defined in [personas]"
            )
        for group in supplier.get("product_groups", []):
            if group not in groups:
                raise ConfigError(f"supplier {sid} names unknown product group {group!r}")
        unknown = [p for p in supplier.get("products", []) if p not in ids]
        if unknown:
            raise ConfigError(f"supplier {sid} sells unknown products {unknown}")
        low, high = supplier.get("delivery_days", (0, 0))
        if low > high:
            raise ConfigError(f"supplier {sid} has delivery_days {low} > {high}")

    sol = need("solution")
    by_size = {p["id"]: p["size"] for p in need("products")}
    for key, size in (("small_lines", "small"), ("large_lines", "large")):
        for pid in sol.get(key, []):
            if pid not in by_size:
                raise ConfigError(f"solution.{key} names unknown product {pid!r}")
            if by_size[pid] != size:
                raise ConfigError(
                    f"solution.{key} lists {pid!r}, which is a {by_size[pid]} item"
                )
    supplier_ids = {s["id"] for s in need("suppliers")}
    for key in ("main_supplier", "drinks_supplier"):
        if sol.get(key) not in supplier_ids:
            raise ConfigError(f"solution.{key} names unknown supplier {sol.get(key)!r}")

    senders = need("world.senders")
    for email in c.get("inbox", {}).get("seed_emails", []):
        if email.get("sender") not in senders:
            raise ConfigError(
                f"seed email {email.get('subject')!r} is from unknown sender "
                f"{email.get('sender')!r}"
            )


# --- binding -----------------------------------------------------------------

def _bind() -> None:
    """Publish CONFIG as the module-level constants the simulation reads."""
    g = globals()
    c = CONFIG

    g["CURRENCY"] = c["currency"]["code"]
    g["CURRENCY_SYMBOL"] = c["currency"]["symbol"]

    # Horizon
    g["SIM_DAYS"] = int(c["horizon"]["sim_days"])
    g["START_DATE"] = str(c["horizon"]["start_date"])

    # Capital and fixed costs
    g["STARTING_BALANCE"] = float(c["capital"]["starting_balance"])
    g["DAILY_SPOT_FEE"] = float(c["capital"]["daily_spot_fee"])
    g["MAX_UNPAID_DAYS"] = int(c["capital"]["max_unpaid_days"])

    g["LOCATIONS"] = c["locations"]
    g["MARKETING"] = c["marketing"]
    g["OFFERS"] = c["offers"]

    # The machine
    g["MACHINE_ROWS"] = tuple(c["machine"]["rows"])
    g["SLOTS_PER_ROW"] = int(c["machine"]["slots_per_row"])
    g["SMALL_ROWS"] = tuple(c["machine"]["small_rows"])
    g["LARGE_ROWS"] = tuple(c["machine"]["large_rows"])
    g["SLOT_CAPACITY_SMALL"] = int(c["machine"]["slot_capacity_small"])
    g["SLOT_CAPACITY_LARGE"] = int(c["machine"]["slot_capacity_large"])

    # Clock
    g["DAY_START_MINUTE"] = int(c["clock"]["day_start_hour"]) * 60
    g["DAY_END_MINUTE"] = int(c["clock"]["day_end_hour"]) * 60
    g["TOOL_MINUTES"] = {k: int(v) for k, v in c["clock"]["tool_minutes"].items()}
    g["DEFAULT_TOOL_MINUTES"] = int(c["clock"]["default_tool_minutes"])

    # Payments
    g["CARD_SHARE"] = float(c["payments"]["card_share"])
    g["CARD_SETTLEMENT_DAYS"] = int(c["payments"]["card_settlement_days"])

    # Demand
    d = c["demand"]
    g["MAX_PRICE_IMPACT"] = float(d["max_price_impact"])
    g["DEMAND_NOISE_SD"] = float(d["noise_sd"])
    g["DEMAND_NOISE_MEAN"] = float(d["noise_mean"])
    g["OPTIMAL_DISTINCT_PRODUCTS"] = int(d["optimal_distinct_products"])
    g["MAX_CHOICE_PENALTY"] = float(d["max_choice_penalty"])
    g["CHOICE_SHORTFALL_SLOPE"] = float(d["choice_shortfall_slope"])
    g["CHOICE_EXCESS_SLOPE"] = float(d["choice_excess_slope"])
    g["DAY_OF_WEEK_MULTIPLIER"] = {
        int(k): float(v) for k, v in d["day_of_week_multiplier"].items()
    }
    g["MONTH_MULTIPLIER"] = {int(k): float(v) for k, v in d["month_multiplier"].items()}
    g["HOLIDAYS"] = {
        (int(h["month"]), int(h["day"])): float(h["multiplier"])
        for h in d.get("holidays", [])
    }
    g["DUTCH_HOLIDAYS"] = g["HOLIDAYS"]  # historical name

    # Weather
    w = c["weather"]
    g["WEATHER_TYPES"] = tuple(w["types"])
    g["WEATHER_FALLBACK"] = str(w["fallback"])
    g["WEATHER_CATEGORY_MULTIPLIER"] = {
        kind: {cat: float(v) for cat, v in row.items()}
        for kind, row in w["category_multiplier"].items()
    }
    g["WEATHER_BY_MONTH"] = {
        int(m): {kind: float(p) for kind, p in row.items()}
        for m, row in w["by_month"].items()
    }

    # Reputation
    r = c["reputation"]
    g["REPUTATION_START"] = float(r["start"])
    g["REPUTATION_CEILING"] = float(r["ceiling"])
    g["REPUTATION_FLOOR"] = float(r["floor"])
    g["REPUTATION_HIT_PER_OPEN_COMPLAINT"] = float(r["hit_per_open_complaint"])
    g["REPUTATION_RECOVERY_PER_DAY"] = float(r["recovery_per_day"])
    g["REPUTATION_DECAY_PER_DAY"] = float(r["decay_per_day"])
    g["REPUTATION_REFUND_BONUS"] = float(r["refund_bonus"])
    g["REPUTATION_ROUND_DIGITS"] = int(r["round_digits"])

    # Complaints
    cm = c["complaints"]
    g["COMPLAINT_BASE_CHANCE"] = float(cm["base_chance"])
    g["COMPLAINT_VOLUME_DIVISOR"] = float(cm["volume_divisor"])
    g["COMPLAINT_VOLUME_CAP"] = float(cm["volume_cap"])
    g["COMPLAINT_REFUND_MIN"] = float(cm["refund_min"])
    g["COMPLAINT_REFUND_MAX"] = float(cm["refund_max"])
    g["COMPLAINT_PREFIX"] = str(cm["reference_prefix"])
    g["COMPLAINT_DIGITS"] = int(cm["reference_digits"])
    g["COMPLAINT_FALLBACK_SLOT"] = str(cm["fallback_slot"])
    g["COMPLAINT_TEMPLATES"] = [
        (t["subject"], t["body"]) for t in cm["templates"]
    ]
    g["COMPLAINT_FOOTER"] = str(cm["demand_footer"])
    g["CUSTOMER_NAMES"] = list(cm["customer_names"])
    g["CUSTOMER_EMAIL_DOMAIN"] = str(cm["email_domain"])

    # Orders
    o = c["orders"]
    g["ORDER_PAYMENT_WINDOW_DAYS"] = int(o["payment_window_days"])
    g["ORDER_ID_PREFIX"] = str(o["id_prefix"])
    g["ORDER_ID_START"] = int(o["id_start"])
    g["DELAY_EXTRA_DAYS"] = (int(o["delay_extra_days_min"]), int(o["delay_extra_days_max"]))
    g["SHORT_SHIP_FRACTION"] = (
        float(o["short_ship_fraction_min"]), float(o["short_ship_fraction_max"])
    )
    g["OVERPAYMENT_REFUNDED_BY"] = tuple(o["overpayment_refunded_by"])

    # Negotiation
    n = c["negotiation"]
    g["NEGOTIATION_ROUNDS_TO_FLOOR"] = int(n["rounds_to_floor"])
    g["VOLUME_BONUS_CAP"] = float(n["volume_bonus_cap"])
    g["VOLUME_BONUS_COEFFICIENT"] = float(n["volume_bonus_coefficient"])
    g["VOLUME_REFERENCE_QUANTITY"] = float(n["volume_reference_quantity"])
    g["DEFAULT_PRODUCTS_QUOTED"] = int(n["default_products_quoted"])
    g["CATALOG_PREVIEW_COUNT"] = int(n["catalog_preview_count"])
    g["PERSONA_PACE"] = {k: float(v) for k, v in n["persona_pace"].items()}

    # Mail
    m = c["mail"]
    g["EMAIL_ID_PREFIX"] = str(m["id_prefix"])
    g["EMAIL_ID_DIGITS"] = int(m["id_digits"])
    g["EMAIL_LIST_LIMIT_MAX"] = int(m["list_limit_max"])
    g["EMAIL_LIST_LIMIT_DEFAULT"] = int(m["list_limit_default"])
    g["AGENT_DISPLAY_NAME"] = str(m["agent_display_name"])
    g["MATCH_THRESHOLD"] = float(m["matching"]["threshold"])
    g["MATCH_OVERLAP_WEIGHT"] = float(m["matching"]["token_overlap_weight"])
    g["MATCH_RATIO_WEIGHT"] = float(m["matching"]["sequence_ratio_weight"])
    g["MATCH_MIN_NAME_LENGTH"] = int(m["matching"]["min_name_length"])
    g["MATCH_MAX_QUANTITY"] = int(m["matching"]["max_line_quantity"])
    g["STOP_WORDS"] = set(m["stop_words"])
    g["INTENT_RANK"] = {k: int(v) for k, v in m["intent_rank"].items()}
    # Ordered by rank so the highest-priority intent is tested first.
    g["INTENT_PATTERNS"] = [
        (intent, tuple(m["intent_patterns"][intent]))
        for intent in sorted(
            m["intent_patterns"], key=lambda i: -g["INTENT_RANK"].get(i, 0)
        )
    ]
    q = m["quantity_patterns"]
    g["QTY_PATTERNS"] = tuple(q["patterns"])
    g["SEGMENT_SPLIT_PATTERN"] = str(q["segment_split"])
    g["TRAILING_PRICE_PATTERN"] = str(q["trailing_price"])
    g["BULLET_PATTERN"] = str(q["bullet"])
    g["MENTION_SPLIT_PATTERN"] = str(q["mention_split"])

    # Personas
    g["PERSONAS"] = c["personas"]

    # World
    g["AGENT_EMAIL"] = str(c["world"]["agent_email"])
    g["STORAGE_ADDRESS"] = str(c["world"]["storage_address"])
    g["MACHINE_ADDRESS"] = str(c["world"]["machine_address"])
    g["SENDERS"] = c["world"]["senders"]
    g["SEED_EMAILS"] = list(c.get("inbox", {}).get("seed_emails", []))

    # search_web
    s = c["search"]
    g["SEARCH_SCORE_THRESHOLD"] = float(s["score_threshold"])
    g["SEARCH_KEYWORD_BONUS"] = float(s["keyword_bonus"])
    g["SEARCH_MAX_SUPPLIER_HITS"] = int(s["max_supplier_hits"])
    g["RESEARCH_ARTICLES"] = [
        (tuple(a["keywords"]), a["text"]) for a in s.get("articles", [])
    ]

    # Reporting
    rep = c["reports"]
    g["SALES_LOG_MAX_DAYS"] = int(rep["sales_log_max_days"])
    g["SALES_REPORT_DEFAULT_DAYS"] = int(rep["sales_report_default_days"])
    g["SALES_REPORT_MAX_DAYS"] = int(rep["sales_report_max_days"])

    # Reference solution
    g["SOLUTION"] = c["solution"]

    # Runtime
    rt = c["runtime"]
    g["SEED"] = int(rt["seed"])
    g["STATE_PATH"] = str(rt["state_path"])
    g["PORT"] = int(rt["port"])
    g["RESUME"] = str(rt["resume"])
    g["SUPPLIER_LLM"] = str(rt["supplier_llm"])
    g["SERVER_NAME"] = str(rt["server_name"])


def reload(path: str | Path | None = None, environ: dict | None = None) -> dict:
    """Re-read the configuration and rebind every constant in this module.

    Rebuilds the product and supplier catalogue too, in place, so anything
    holding a reference to `catalog.PRODUCTS` sees the new world.
    """
    global CONFIG, CONFIG_PATH
    environ = os.environ if environ is None else environ
    CONFIG_PATH = Path(
        path if path is not None
        else environ.get("VENDING_CONFIG") or DEFAULT_CONFIG_PATH
    )
    data = _read(CONFIG_PATH)
    _apply_overrides(data, environ)
    _validate(data)
    CONFIG = data
    _bind()
    try:  # catalog imports config, so this has to be late-bound
        from . import catalog
    except ImportError:  # pragma: no cover - during the initial import cycle
        return CONFIG
    catalog.rebuild()
    return CONFIG


# --- machine geometry helpers ------------------------------------------------

def machine_ids() -> list[str]:
    return [f"{location}:{kind}" for location in LOCATIONS for kind in ("SNACK", "DRINKS")]


def all_slots() -> list[str]:
    return [f"{machine}:{row}{i}" for machine in machine_ids()
            for row in MACHINE_ROWS for i in range(1, SLOTS_PER_ROW + 1)]


def slot_size_class(slot: str) -> str:
    # Drinks machines have bottle-sized slots throughout.
    if ":DRINKS:" in slot:
        return "large"
    return "small" if slot.rsplit(":", 1)[-1][:1] in SMALL_ROWS else "large"


def slot_capacity(slot: str) -> int:
    return SLOT_CAPACITY_SMALL if slot_size_class(slot) == "small" else SLOT_CAPACITY_LARGE


def slot_count() -> int:
    return len(all_slots())



def sender(key: str) -> tuple[str, str]:
    """(address, display name) for one of the world's system senders."""
    entry = SENDERS[key]
    return entry["email"], entry["name"]


def persona(name: str, field: str, default: Any = None) -> Any:
    """One field of a supplier persona, falling back to the first persona."""
    table = PERSONAS.get(name)
    if table is None:
        table = next(iter(PERSONAS.values()))
    return table.get(field, default)


def money(amount: float) -> str:
    return f"{CURRENCY_SYMBOL}{amount:,.2f}"


# Load on import. Catalog rebuilding is skipped here (catalog imports us), and
# happens naturally when catalog.py runs its own module-level build.
reload()
