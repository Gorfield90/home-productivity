"""Watch list: each person adds series and movies, and the app picks the next one to watch at random."""

import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import KINDS, Show, User
from .shopping import FormError, bump

WATCHED_SHOWN = 20  # history shown under the pool

_rng = random.SystemRandom()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clean_title(raw: str) -> str:
    title = " ".join(raw.split())[:120]
    if not title:
        raise FormError("Type the name of a series or movie.")
    return title


def pool(session: Session) -> list[Show]:
    """What is waiting to be picked, oldest first."""
    return list(session.scalars(select(Show).where(Show.picked_at.is_(None), Show.watched_at.is_(None))
                                .order_by(Show.created_at, Show.id)))


def current(session: Session) -> Show | None:
    """The one picked to watch next, if any."""
    return session.scalar(select(Show).where(Show.picked_at.is_not(None), Show.watched_at.is_(None))
                          .order_by(Show.picked_at.desc()).limit(1))


def watched(session: Session, limit: int = WATCHED_SHOWN) -> list[Show]:
    return list(session.scalars(select(Show).where(Show.watched_at.is_not(None))
                                .order_by(Show.watched_at.desc(), Show.id.desc()).limit(limit)))


def by_person(shows: list[Show], users: list[User]) -> list[tuple[User, list[Show]]]:
    """The pool split per person, everyone listed even with nothing in it."""
    return [(u, [s for s in shows if s.added_by_id == u.id]) for u in users]


def add_show(session: Session, user: User, title: str, kind: str) -> Show:
    title = clean_title(title)
    if kind not in KINDS:
        kind = "series"
    waiting = pool(session) + [s for s in [current(session)] if s]
    if any(s.title.casefold() == title.casefold() for s in waiting):
        raise FormError(f"{title} is already on the list.")
    show = Show(title=title, kind=kind, added_by=user)
    session.add(show)
    bump(session)
    return show


def pick(session: Session, user: User, kind: str = "", rng=None) -> Show:
    """Pick the next show at random. Each person whose shows qualify gets the same chance, however many
    they added, then one of theirs is drawn. A show already picked goes back into the pool and is
    left out of this draw, so "pick another" always changes it."""
    rng = rng or _rng
    was = current(session)
    candidates = [s for s in pool(session) if not kind or s.kind == kind]
    if not candidates:
        what = {"series": "series", "movie": "movies"}.get(kind, "shows")
        raise FormError(f"No other {what} to pick from. Add some first." if was else f"No {what} to pick from. Add some first.")
    people = sorted({s.added_by_id for s in candidates})
    person = rng.choice(people)
    show = rng.choice([s for s in candidates if s.added_by_id == person])
    if was:
        was.picked_at, was.picked_by_id = None, None
    show.picked_at, show.picked_by = _now(), user
    bump(session)
    return show


def put_back(session: Session, show: Show) -> None:
    show.picked_at, show.picked_by = None, None
    bump(session)


def mark_watched(session: Session, show: Show) -> None:
    show.watched_at = _now()
    bump(session)
