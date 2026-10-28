"""Shopping list logic, kept out of the web layer so it can be tested on its own."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import DEFAULT_CATEGORIES, Category, Item, Revision, User

# "2 milk", "1.5 kg potatoes", "6x eggs", "500g flour": a leading amount becomes the quantity.
_LEADING_QTY = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(x|×|kg|g|l|ml|cl|un|pcs)?\s+(\S.*)$", re.IGNORECASE)
# "milk x2", "eggs ×12": a trailing count does too.
_TRAILING_QTY = re.compile(r"^(\S.*?)\s+[x×](\d+)$", re.IGNORECASE)

RECENT_LIMIT = 24

# First guess at the aisle for products never bought before (English and Portuguese), by default aisle name.
# Each entry matches the start of a word, so "tomato" also covers "tomatoes"; the longest match wins, so
# "kitchen roll" beats "roll" and "pasta de dentes" beats "pasta". After the first time, the aisle a
# product was last put in wins.
AISLE_WORDS = {
    "Fruit & veg": "apple, banana, orange, lemon, lime, pear, grape, strawberr, melon, tomat, potato, onion, garlic, "
                   "carrot, lettuce, salad, spinach, broccoli, pepper, cucumber, courgette, zucchini, avocado, "
                   "mushroom, parsley, coriander, fruit, maçã, laranja, limão, pera, uva, morango, batata, cebola, "
                   "alho, cenoura, alface, espinafre, brócol, pimento, pepino, courgete, abacate, cogumelo, salsa, "
                   "coentros",
    "Bakery": "bread, baguette, bread roll, croissant, bun, cake, pão, broa, bolo",
    "Meat & fish": "chicken, beef, pork, mince, sausage, ham, bacon, turkey, fish, salmon, cod, tuna, shrimp, prawn, "
                   "frango, vaca, porco, carne, salsicha, fiambre, presunto, peru, peixe, salmão, bacalhau, atum, "
                   "camarão",
    "Dairy & eggs": "milk, cheese, yogurt, yoghurt, butter, cream, egg, leite, queijo, iogurte, manteiga, natas, ovo",
    "Pantry": "rice, pasta, spaghetti, flour, sugar, salt, oil, olive oil, vinegar, sauce, cereal, oat, coffee, tea, "
              "honey, jam, beans, lentil, chickpea, arroz, massa, esparguete, farinha, açúcar, sal, azeite, óleo, "
              "vinagre, molho, cereais, aveia, café, chá, mel, compota, feijão, lentilhas, grão",
    "Frozen": "frozen, ice cream, peas, pizza, congelad, gelado, ervilhas",
    "Drinks": "water, juice, orange juice, beer, wine, soda, cola, água, sumo, sumo de laranja, cerveja, vinho, "
              "refrigerante",
    "Household": "toilet paper, toilet roll, kitchen roll, kitchen paper, detergent, washing up, dish soap, "
                 "dishwasher, bin bag, sponge, bleach, foil, cling film, papel higiénico, papel de cozinha, "
                 "detergente, lixívia, esponja, sacos do lixo, papel de alumínio",
    "Personal care": "shampoo, conditioner, toothpaste, toothbrush, deodorant, razor, nappies, diapers, champô, "
                     "amaciador, pasta de dentes, escova de dentes, desodorizante, fraldas",
}


class FormError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_entry(text: str) -> tuple[str, str]:
    """Split what was typed into (name, quantity). The name keeps its own capitalisation."""
    text = " ".join(text.split())
    if m := _LEADING_QTY.match(text):
        amount, unit, name = m.groups()
        unit = (unit or "").lower()
        if unit in ("", "x", "×"):
            return name, amount
        return name, f"{amount} {unit}" if unit in ("un", "pcs") else f"{amount}{unit}"
    if m := _TRAILING_QTY.match(text):
        return m.group(1), m.group(2)
    return text, ""


def clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise FormError("Type what you need.")
    if len(name) > 120:
        raise FormError("That name is too long (120 characters at most).")
    return name[:1].upper() + name[1:]


def revision(session: Session) -> int:
    return session.scalar(select(Revision.n).where(Revision.id == 1)) or 0


def bump(session: Session) -> None:
    """Mark that something changed, so the other person's open list refreshes."""
    row = session.get(Revision, 1)
    if row is None:
        session.add(Revision(id=1, n=1))
    else:
        row.n += 1


def ensure_categories(session: Session) -> None:
    """Give a brand-new household the default aisles. Never touches categories once there are some."""
    if session.scalar(select(func.count(Category.id))):
        return
    for i, (name, icon) in enumerate(DEFAULT_CATEGORIES):
        session.add(Category(name=name, icon=icon, position=i))


def categories(session: Session) -> list[Category]:
    return list(session.scalars(select(Category).order_by(Category.position, Category.id)))


def last_category_id(session: Session, name: str) -> int | None:
    """The category this product had the last time it was on the list."""
    return session.scalar(
        select(Item.category_id)
        .where(func.lower(Item.name) == name.lower())
        .order_by(Item.created_at.desc(), Item.id.desc())
        .limit(1)
    )


def guess_category_id(session: Session, name: str) -> int | None:
    """The default aisle with the longest phrase found at a word start in the name, if that aisle still exists."""
    text = name.lower()
    best, best_len = None, 0
    for aisle, phrases in AISLE_WORDS.items():
        for phrase in phrases.split(", "):
            if len(phrase) > best_len and re.search(rf"(?<!\w){re.escape(phrase)}", text):
                best, best_len = aisle, len(phrase)
    if best is None:
        return None
    return session.scalar(select(Category.id).where(Category.name == best))


def on_list(session: Session) -> list[Item]:
    return list(session.scalars(select(Item).where(Item.bought_at.is_(None)).order_by(Item.created_at, Item.id)))


def add_item(session: Session, user: User, text: str, category_id: int | None = None,
             quantity: str = "") -> tuple[Item, bool]:
    """Put something on the list. Returns (item, created).

    Typing a product that is already on the list (and not ticked yet) doesn't add a second line:
    the new quantity, if any, replaces the old one. Without a category, the product keeps the one it
    had last time.
    """
    name, parsed_qty = parse_entry(text)
    name = clean_name(name)
    quantity = (quantity or parsed_qty).strip()[:40]
    if category_id is not None and session.get(Category, category_id) is None:
        category_id = None

    existing = session.scalar(
        select(Item).where(Item.bought_at.is_(None), Item.checked_at.is_(None), func.lower(Item.name) == name.lower())
    )
    if existing:
        if quantity:
            existing.quantity = quantity
        if category_id is not None:
            existing.category_id = category_id
        bump(session)
        return existing, False

    if category_id is None:
        category_id = last_category_id(session, name) or guess_category_id(session, name)
    item = Item(name=name, quantity=quantity, category_id=category_id, added_by=user)
    session.add(item)
    bump(session)
    return item, True


def toggle(session: Session, user: User, item: Item) -> None:
    if item.checked_at is None:
        item.checked_at, item.checked_by = _now(), user
    else:
        item.checked_at, item.checked_by = None, None
    bump(session)


def clear_ticked(session: Session) -> int:
    """Finish a shop: everything ticked leaves the list and becomes history. Returns how many."""
    result = session.execute(
        update(Item).where(Item.bought_at.is_(None), Item.checked_at.is_not(None)).values(bought_at=_now())
    )
    if result.rowcount:
        bump(session)
    return result.rowcount


def recently_bought(session: Session, limit: int = RECENT_LIMIT) -> list[Item]:
    """The latest purchase of each product, newest first, leaving out what is on the list already."""
    current = {n.lower() for n in session.scalars(select(Item.name).where(Item.bought_at.is_(None)))}
    seen: set[str] = set()
    out = []
    for item in session.scalars(select(Item).where(Item.bought_at.is_not(None)).order_by(Item.bought_at.desc())):
        key = item.name.lower()
        if key in seen or key in current:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def forget(session: Session, name: str) -> None:
    """Drop a product from "recently bought" (deletes its history, never what is on the list)."""
    for item in session.scalars(select(Item).where(Item.bought_at.is_not(None), func.lower(Item.name) == name.lower())):
        session.delete(item)
    bump(session)


@dataclass
class Group:
    category: Category | None
    items: list[Item] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.category.name if self.category else "Other"

    @property
    def icon(self) -> str:
        return self.category.icon if self.category else "🛒"


def grouped(items: list[Item], cats: list[Category]) -> list[Group]:
    """Unticked items by aisle in shop order, things without an aisle last."""
    by_id = {c.id: Group(c) for c in cats}
    other = Group(None)
    for item in items:
        by_id.get(item.category_id, other).items.append(item)
    return [g for g in [*by_id.values(), other] if g.items]


# ---- categories ----

def clean_category(name: str, icon: str) -> tuple[str, str]:
    name = " ".join(name.split())
    if not name:
        raise FormError("A category needs a name.")
    if len(name) > 60:
        raise FormError("That name is too long (60 characters at most).")
    return name, icon.strip()[:16]


def add_category(session: Session, name: str, icon: str) -> Category:
    name, icon = clean_category(name, icon)
    last = session.scalar(select(func.max(Category.position))) or 0
    cat = Category(name=name, icon=icon, position=last + 1)
    session.add(cat)
    bump(session)
    return cat


def move_category(session: Session, cat: Category, step: int) -> None:
    """Move an aisle one place up (-1) or down (+1) in the shop order."""
    cats = categories(session)
    i = cats.index(cat)
    j = i + step
    if 0 <= j < len(cats):
        cats[i], cats[j] = cats[j], cats[i]
        for pos, c in enumerate(cats):
            c.position = pos
        bump(session)


def delete_category(session: Session, cat: Category) -> None:
    """Delete an aisle. Its items stay on the list, under "Other"."""
    session.execute(update(Item).where(Item.category_id == cat.id).values(category_id=None))
    session.delete(cat)
    bump(session)
