"""Recipe logic: parsing ingredient lines, search, and sending ingredients to the shopping list."""

import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import Ingredient, Item, Recipe, User
from .shopping import FormError, add_item, bump

# "2", "1.5", "1,5", "1/2", "1 1/2", "½" followed by an optional unit, then the ingredient.
_AMOUNT = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?|[½¼¾⅓⅔])"
_UNITS = (
    "kg|g|gr|mg|l|dl|cl|ml|tbsp|tbs|tsp|cups?|oz|lb|pinch(?:es)?|cloves?|cans?|tins?|slices?|bunch(?:es)?|"
    "handfuls?|sprigs?|packs?|packets?|"
    "colher(?:es)?(?: de (?:sopa|chá|sobremesa))?|c\\.(?: de )?(?:sopa|chá)|chávenas?|copos?|dentes?|latas?|"
    "pitadas?|fatias?|molhos?|pacotes?|un"
)
_LINE = re.compile(rf"^(?P<amount>{_AMOUNT})\s*(?P<unit>(?:{_UNITS})\b\.?)?\s*(?:of |de |d')?(?P<name>\S.*)$",
                   re.IGNORECASE)

_TO_TASTE = re.compile(r"(?:^|\s|,)(?:q\.?b\.?|to taste)(?=\s|,|$)", re.IGNORECASE)


def parse_line(line: str) -> tuple[str, str]:
    """Split an ingredient line into (name, quantity): "1 tbsp olive oil" → ("Olive oil", "1 tbsp").

    Anything after a comma or in brackets is preparation ("2 onions, chopped"), so it stays out of the name.
    """
    line = " ".join(line.split())
    name, qty = line, ""
    # "sal q.b." / "salt to taste": no amount to buy, just the product.
    if m := _TO_TASTE.search(line):
        name, qty = (line[:m.start()] + line[m.end():]).strip(" ,"), "to taste"
    elif m := _LINE.match(line):
        qty = " ".join(p for p in (m["amount"], m["unit"]) if p)
        name = m["name"]
    name = re.sub(r"\s*\(.*?\)", "", name).split(",")[0].strip() or line
    return name[:1].upper() + name[1:120], qty[:40]


def set_ingredients(recipe: Recipe, text: str) -> None:
    recipe.ingredients = []
    for i, line in enumerate(l.strip(" -•*\t") for l in text.splitlines()):
        if line:
            name, qty = parse_line(line)
            recipe.ingredients.append(Ingredient(position=i, line=line[:200], name=name, quantity=qty))


def _int(raw, low: int, high: int, what: str) -> int | None:
    raw = str(raw or "").strip()
    if not raw:
        return None
    if not raw.isdigit() or not low <= int(raw) <= high:
        raise FormError(f"{what} must be a whole number from {low} to {high}.")
    return int(raw)


def apply_form(recipe: Recipe, form) -> None:
    """Fill a recipe from the form, or raise FormError (leaving it half-filled: roll back)."""
    title = " ".join(str(form.get("title", "")).split())
    if not title:
        raise FormError("A recipe needs a name.")
    url = str(form.get("source_url", "")).strip()
    if url and not url.startswith(("http://", "https://")):
        raise FormError("The link must start with http:// or https://.")
    recipe.title = title[:120]
    recipe.servings = _int(form.get("servings"), 1, 50, "Servings")
    recipe.minutes = _int(form.get("minutes"), 1, 2000, "Time")
    recipe.source_url = url[:500]
    recipe.tags = ", ".join(t.strip().lower() for t in str(form.get("tags", "")).split(",") if t.strip())[:200]
    recipe.steps = str(form.get("steps", "")).strip()
    recipe.notes = str(form.get("notes", "")).strip()
    set_ingredients(recipe, str(form.get("ingredients", "")))


def form_values(recipe: Recipe) -> dict:
    return {"title": recipe.title, "servings": recipe.servings or "", "minutes": recipe.minutes or "",
            "source_url": recipe.source_url, "tags": recipe.tags, "steps": recipe.steps, "notes": recipe.notes,
            "ingredients": "\n".join(i.line for i in recipe.ingredients)}


def search(session: Session, q: str = "", tag: str = "") -> list[Recipe]:
    stmt = select(Recipe)
    for word in q.lower().split():
        like = f"%{word}%"
        stmt = stmt.where(or_(func.lower(Recipe.title).like(like), func.lower(Recipe.tags).like(like),
                              Recipe.id.in_(select(Ingredient.recipe_id).where(func.lower(Ingredient.line).like(like)))))
    if tag:
        stmt = stmt.where(func.lower(", " + Recipe.tags + ",").like(f"%, {tag.lower()},%"))
    return list(session.scalars(stmt.order_by(func.lower(Recipe.title))))


def all_tags(session: Session) -> list[str]:
    tags = set()
    for raw in session.scalars(select(Recipe.tags)):
        tags.update(t.strip() for t in raw.split(",") if t.strip())
    return sorted(tags)


def _buyable(quantity: str) -> str:
    return "" if quantity == "to taste" else quantity


def to_shopping(session: Session, user: User, recipe: Recipe, ingredient_ids: set[int]) -> list[str]:
    """Put the chosen ingredients on the shopping list. Returns the names added.

    An ingredient already on the list keeps its line; its quantities are joined ("2 + 1 tbsp"), and the
    note says which recipes it is for.
    """
    added = []
    for ing in recipe.ingredients:
        if ing.id not in ingredient_ids:
            continue
        existing = session.scalar(select(Item).where(Item.bought_at.is_(None), Item.checked_at.is_(None),
                                                     func.lower(Item.name) == ing.name.lower()))
        if existing:
            if _buyable(ing.quantity):
                existing.quantity = (f"{existing.quantity} + {ing.quantity}" if existing.quantity else ing.quantity)[:40]
            if recipe.title not in existing.note:
                existing.note = f"{existing.note}, {recipe.title}" if existing.note else f"For {recipe.title}"
            bump(session)
        else:
            item, _ = add_item(session, user, ing.name, quantity=_buyable(ing.quantity))
            item.note = f"For {recipe.title}"
        added.append(ing.name)
    return added
