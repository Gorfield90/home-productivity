"""Plants: who watered what and when, and which ones need water today."""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Plant, User, Watering
from .shopping import FormError, bump

HISTORY_SHOWN = 30
MAX_EVERY = 365


@dataclass
class Status:
    """Where a plant stands today: its next watering day (if it has a schedule) and how that reads."""

    plant: Plant
    last: Watering | None
    next_day: date | None
    text: str
    kind: str  # "late", "today", "soon" or "" (fine / no schedule), the same names as the to-do due chips

    @property
    def needs_water(self) -> bool:
        return self.kind in ("late", "today")

    @property
    def watered_today(self) -> bool:
        return self.kind == "done"


def _days(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


def ago(day: date, today: date) -> str:
    n = (today - day).days
    if n <= 0:
        return "today"
    if n == 1:
        return "yesterday"
    if n < 7:
        return f"{n} days ago"
    return f"on {day.day} {day.strftime('%b')}" + (f" {day.year}" if day.year != today.year else "")


def status(plant: Plant, today: date) -> Status:
    last = plant.last_watering
    if last is not None and last.day >= today:
        nxt = last.day + timedelta(days=plant.every_days) if plant.every_days else None
        return Status(plant, last, nxt, "Watered today", "done")
    if not plant.every_days:
        return Status(plant, last, None, "", "")
    if last is None:
        return Status(plant, None, today, "Needs water", "today")
    nxt = last.day + timedelta(days=plant.every_days)
    left = (nxt - today).days
    if left < 0:
        return Status(plant, last, nxt, f"{_days(-left)} late", "late")
    if left == 0:
        return Status(plant, last, nxt, "Water today", "today")
    if left == 1:
        return Status(plant, last, nxt, "Tomorrow", "soon")
    return Status(plant, last, nxt, f"In {_days(left)}", "")


def overview(session: Session, today: date) -> list[Status]:
    """Every plant, the ones needing water first (latest first), then by next watering day; plants without a
    schedule last, the longest unwatered first."""
    rows = [status(p, today) for p in session.scalars(select(Plant))]
    far = date.max

    def key(s: Status):
        if s.next_day is not None and s.kind != "done":
            return (0, s.next_day, s.plant.name.casefold())
        if s.kind == "done":
            return (1, s.next_day or far, s.plant.name.casefold())
        return (2, s.last.day if s.last else date.min, s.plant.name.casefold())

    return sorted(rows, key=key)


def due_count(session: Session, today: date) -> int:
    """How many plants need water today (late or due), for the badge in the menu."""
    last = (select(Watering.plant_id, func.max(Watering.day).label("day"))
            .group_by(Watering.plant_id).subquery())
    rows = session.execute(select(Plant.every_days, last.c.day).outerjoin(last, last.c.plant_id == Plant.id)
                           .where(Plant.every_days.is_not(None))).all()
    return sum(1 for every, day in rows if day is None or day + timedelta(days=every) <= today)


def _clean(raw: str, limit: int) -> str:
    return " ".join(str(raw or "").split())[:limit]


def apply_form(plant: Plant, form) -> None:
    name = _clean(form.get("name", ""), 60)
    if not name:
        raise FormError("Give the plant a name.")
    raw = str(form.get("every_days", "") or "").strip()
    every = None
    if raw:
        try:
            every = int(raw)
        except ValueError:
            raise FormError("Water every… should be a number of days, like 7.") from None
        if not 1 <= every <= MAX_EVERY:
            raise FormError(f"Water every… should be between 1 and {MAX_EVERY} days.")
    plant.name, plant.every_days = name, every
    plant.spot = _clean(form.get("spot", ""), 60)
    plant.notes = str(form.get("notes", "") or "").strip()[:2000]


def form_values(plant: Plant) -> dict:
    return {"name": plant.name, "spot": plant.spot, "every_days": plant.every_days or "", "notes": plant.notes}


def water(session: Session, plant: Plant, user: User, day: date, today: date) -> Watering | None:
    """Log a watering. Once per plant per day: a second tap the same day changes nothing."""
    if day > today:
        raise FormError("That day hasn't happened yet.")
    if any(w.day == day for w in plant.waterings):
        return None
    w = Watering(day=day, by=user)
    plant.waterings.append(w)
    plant.waterings.sort(key=lambda x: (x.day, x.id or 0), reverse=True)
    bump(session)
    return w


def undo_today(session: Session, plant: Plant, today: date) -> None:
    for w in [w for w in plant.waterings if w.day == today]:
        plant.waterings.remove(w)
    bump(session)


def history(plant: Plant, limit: int = HISTORY_SHOWN) -> list[Watering]:
    return plant.waterings[:limit]


def average_gap(plant: Plant) -> int | None:
    """Days between waterings on average, from the history (to help pick a schedule)."""
    days = sorted({w.day for w in plant.waterings})
    if len(days) < 3:
        return None
    return round((days[-1] - days[0]).days / (len(days) - 1))
