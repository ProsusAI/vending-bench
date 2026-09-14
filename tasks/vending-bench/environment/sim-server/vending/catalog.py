"""Products and suppliers, built from the [[products]] and [[suppliers]]
tables in config.toml.

Nothing is hardcoded here: this module only turns the configured tables into
frozen dataclasses and indexes them. Editing the catalogue means editing
config.toml (or pointing VENDING_CONFIG at a variant of it).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    size: str            # "small" | "large"
    category: str        # keys of config.WEATHER_CATEGORY_MULTIPLIER
    true_cost: float     # honest wholesale unit cost
    reference_price: float
    base_sales: float    # units/day at reference price, neutral conditions
    elasticity: float    # negative
    aliases: tuple[str, ...] = ()
    shelf_life_days: int | None = None

    @property
    def search_terms(self) -> tuple[str, ...]:
        return (self.name.lower(), self.id.replace("-", " ")) + self.aliases


@dataclass(frozen=True)
class Supplier:
    id: str
    name: str
    email: str
    persona: str
    blurb: str
    product_ids: tuple[str, ...]
    markup: float          # list price = true_cost * markup
    floor: float           # best reachable price as a fraction of list price
    min_order_value: float
    delivery_days: tuple[int, int]
    contact: str = "Sales"
    delay_chance: float = 0.0
    bait_switch_chance: float = 0.0
    ghost_chance: float = 0.0             # ignores an email entirely
    collapse_chance_per_day: float = 0.0  # goes out of business mid-year
    keywords: tuple[str, ...] = ()


#: product_id -> Product. Rebuilt in place by rebuild(), so importers keep
#: their reference across a config.reload().
PRODUCTS: dict[str, Product] = {}

#: supplier_id -> Supplier, same contract as PRODUCTS.
SUPPLIERS: dict[str, Supplier] = {}

#: group name -> tuple of product ids, from [product_groups].
PRODUCT_GROUPS: dict[str, tuple[str, ...]] = {}


def _build_product(row: dict) -> Product:
    return Product(
        id=row["id"],
        name=row["name"],
        size=row["size"],
        category=row["category"],
        true_cost=float(row["true_cost"]),
        reference_price=float(row["reference_price"]),
        base_sales=float(row["base_sales"]),
        elasticity=float(row["elasticity"]),
        aliases=tuple(row.get("aliases", ())),
        shelf_life_days=row.get("shelf_life_days"),
    )


def _supplier_products(row: dict) -> tuple[str, ...]:
    """Expand a supplier's product groups, then its explicit extras, in order."""
    ids: list[str] = []
    for group in row.get("product_groups", ()):
        for pid in PRODUCT_GROUPS[group]:
            if pid not in ids:
                ids.append(pid)
    for pid in row.get("products", ()):
        if pid not in ids:
            ids.append(pid)
    return tuple(ids)


def _build_supplier(row: dict) -> Supplier:
    low, high = row["delivery_days"]
    return Supplier(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        persona=row["persona"],
        blurb=row["blurb"],
        product_ids=_supplier_products(row),
        markup=float(row["markup"]),
        floor=float(row["floor"]),
        min_order_value=float(row["min_order_value"]),
        delivery_days=(int(low), int(high)),
        contact=row.get("contact", "Sales"),
        delay_chance=float(row.get("delay_chance", 0.0)),
        bait_switch_chance=float(row.get("bait_switch_chance", 0.0)),
        ghost_chance=float(row.get("ghost_chance", 0.0)),
        collapse_chance_per_day=float(row.get("collapse_chance_per_day", 0.0)),
        keywords=tuple(row.get("keywords", ())),
    )


def rebuild() -> None:
    """(Re)build the catalogue from the currently loaded configuration."""
    c = config.CONFIG
    PRODUCTS.clear()
    for row in c["products"]:
        product = _build_product(row)
        PRODUCTS[product.id] = product

    PRODUCT_GROUPS.clear()
    for name, members in (c.get("product_groups") or {}).items():
        PRODUCT_GROUPS[name] = tuple(members)

    SUPPLIERS.clear()
    for row in c["suppliers"]:
        supplier = _build_supplier(row)
        SUPPLIERS[supplier.id] = supplier


def supplier_for_email(address: str) -> Supplier | None:
    address = (address or "").strip().lower()
    for sup in SUPPLIERS.values():
        if sup.email.lower() == address:
            return sup
    return None


rebuild()
