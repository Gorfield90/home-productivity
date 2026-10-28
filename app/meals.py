"""Meal planner: a week of lunches and dinners, from recipes or a few words, and shopping for them."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import SLOTS, Meal, Recipe, User
from .shopping import FormError, bump
from . import recipes


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def parse_week(raw: str | None, today: date) -> date:
    """The Monday of the week asked for (any day in it will do), this week if none or nonsense."""
    try:
        return monday(date.fromisoformat(raw)) if raw else monday(today)
    except ValueError:
        return monday(today)


@dataclass
class Day:
    day: date
    slots: dict[str, list[Meal]] = field(default_factory=lambda: {s: [] for s in SLOTS})

    @property
    def empty(self) -> bool:
        return not any(self.slots.values())


def week(session: Session, start: date) -> list[Day]:
    days = [Day(start + timedelta(days=i)) for i in range(7)]
    meals = session.scalars(select(Meal).where(Meal.day >= start, Meal.day < start + timedelta(days=7))
                            .order_by(Meal.day, Meal.created_at, Meal.id))
    for meal in meals:
        days[(meal.day - start).days].slots.setdefault(meal.slot, []).append(meal)
    return days


def add_meal(session: Session, user: User, day: str, slot: str, recipe_id, text: str) -> Meal:
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise FormError("Pick a day.") from None
    if slot not in SLOTS:
        raise FormError("Pick lunch or dinner.")
    recipe = None
    try:
        recipe = session.get(Recipe, int(recipe_id)) if recipe_id not in (None, "") else None
    except (TypeError, ValueError):
        pass
    text = " ".join(text.split())[:120]
    if recipe is None and not text:
        raise FormError("Pick a recipe or type what you'll eat.")
    meal = Meal(day=d, slot=slot, recipe=recipe, text="" if recipe else text, created_by=user)
    session.add(meal)
    bump(session)
    return meal


def week_recipes(session: Session, start: date) -> list[tuple[Recipe, list[Meal]]]:
    """Each recipe planned this week, once, with the meals it is planned for, in plan order."""
    out: dict[int, tuple[Recipe, list[Meal]]] = {}
    for d in week(session, start):
        for meals in d.slots.values():
            for meal in meals:
                if meal.recipe:
                    out.setdefault(meal.recipe.id, (meal.recipe, []))[1].append(meal)
    return list(out.values())


def week_to_shopping(session: Session, user: User, start: date, ingredient_ids: set[int]) -> int:
    added = 0
    for recipe, _ in week_recipes(session, start):
        added += len(recipes.to_shopping(session, user, recipe, ingredient_ids))
    return added


def keep_names(session: Session, recipe: Recipe) -> None:
    """Before a recipe is deleted: its planned meals keep its name as plain text."""
    session.execute(update(Meal).where(Meal.recipe_id == recipe.id).values(text=recipe.title[:120]))
