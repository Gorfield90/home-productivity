"""To-do list logic, kept out of the web layer so it can be tested on its own."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import Todo, TodoList, User
from .shopping import FormError, bump

DONE_SHOWN = 30  # finished tasks shown under a list; older ones only count
SOON_DAYS = 7    # "Coming up" on the overview: overdue plus the next week


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_lists(session: Session) -> None:
    """Give a brand-new household one shared list to start from."""
    if not session.scalar(select(func.count(TodoList.id))):
        session.add(TodoList(name="Home", icon="🏠", position=0))


def visible(user: User):
    """Lists this person may see: the shared ones and their own."""
    return or_(TodoList.owner_id.is_(None), TodoList.owner_id == user.id)


def lists(session: Session, user: User) -> list[TodoList]:
    return list(session.scalars(select(TodoList).where(visible(user)).order_by(TodoList.position, TodoList.id)))


def get_list(session: Session, user: User, list_id: int) -> TodoList | None:
    tl = session.get(TodoList, list_id)
    return tl if tl is not None and (tl.owner_id is None or tl.owner_id == user.id) else None


def get_todo(session: Session, user: User, todo_id: int) -> Todo | None:
    todo = session.get(Todo, todo_id)
    return todo if todo is not None and get_list(session, user, todo.list_id) else None


def open_counts(session: Session, user: User) -> dict[int, int]:
    rows = session.execute(
        select(Todo.list_id, func.count(Todo.id)).join(TodoList).where(visible(user), Todo.done_at.is_(None))
        .group_by(Todo.list_id)
    )
    return dict(rows.all())


def _order(q):
    # Dated tasks first, soonest first; then undated ones in the order they were added.
    return q.order_by(Todo.due.is_(None), Todo.due, Todo.created_at, Todo.id)


def open_todos(session: Session, tl: TodoList) -> list[Todo]:
    return list(session.scalars(_order(select(Todo).where(Todo.list_id == tl.id, Todo.done_at.is_(None)))))


def done_todos(session: Session, tl: TodoList) -> tuple[list[Todo], int]:
    """The most recently finished tasks, and how many finished tasks there are in all."""
    q = select(Todo).where(Todo.list_id == tl.id, Todo.done_at.is_not(None))
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    return list(session.scalars(q.order_by(Todo.done_at.desc()).limit(DONE_SHOWN))), total


def coming_up(session: Session, user: User, today: date) -> list[Todo]:
    """Open tasks overdue or due within a week, across every list this person sees."""
    q = (select(Todo).join(TodoList).where(visible(user), Todo.done_at.is_(None), Todo.due.is_not(None),
                                           Todo.due <= today + timedelta(days=SOON_DAYS)))
    return list(session.scalars(_order(q)))


# ---- forms ----

def clean_title(title: str) -> str:
    title = " ".join(title.split())
    if not title:
        raise FormError("Type what needs doing.")
    if len(title) > 200:
        raise FormError("That is too long (200 characters at most).")
    return title[:1].upper() + title[1:]


def parse_due(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise FormError("The due date doesn't look like a date.") from None


def pick_assignee(session: Session, tl: TodoList, raw) -> int | None:
    """Only shared lists have assignees; anyone not a known user means "anyone"."""
    if not tl.shared:
        return None
    try:
        uid = int(raw)
    except (TypeError, ValueError):
        return None
    return uid if session.get(User, uid) else None


def add_todo(session: Session, user: User, tl: TodoList, title: str, due: str = "", assignee=None) -> Todo:
    todo = Todo(list_id=tl.id, title=clean_title(title), due=parse_due(due),
                assignee_id=pick_assignee(session, tl, assignee), created_by=user)
    session.add(todo)
    bump(session)
    return todo


def toggle(session: Session, user: User, todo: Todo) -> None:
    if todo.done_at is None:
        todo.done_at, todo.done_by = _now(), user
    else:
        todo.done_at, todo.done_by = None, None
    bump(session)


def clear_done(session: Session, tl: TodoList) -> int:
    done = session.scalars(select(Todo).where(Todo.list_id == tl.id, Todo.done_at.is_not(None))).all()
    for todo in done:
        session.delete(todo)
    if done:
        bump(session)
    return len(done)


def clean_list(name: str, icon: str) -> tuple[str, str]:
    name = " ".join(name.split())
    if not name:
        raise FormError("A list needs a name.")
    if len(name) > 60:
        raise FormError("That name is too long (60 characters at most).")
    return name, icon.strip()[:16]


def add_list(session: Session, user: User, name: str, icon: str, personal: bool) -> TodoList:
    name, icon = clean_list(name, icon)
    last = session.scalar(select(func.max(TodoList.position))) or 0
    tl = TodoList(name=name, icon=icon, owner_id=user.id if personal else None, position=last + 1)
    session.add(tl)
    bump(session)
    return tl


def set_personal(session: Session, user: User, tl: TodoList, personal: bool) -> None:
    """Make a list shared or personal. A personal list has no assignees, so they are cleared."""
    tl.owner_id = user.id if personal else None
    if personal:
        for todo in session.scalars(select(Todo).where(Todo.list_id == tl.id, Todo.assignee_id.is_not(None))):
            todo.assignee_id = None


# ---- display ----

@dataclass
class Due:
    text: str
    kind: str  # "late", "today", "soon" or "" (later)


def due_label(due: date | None, today: date) -> Due | None:
    if due is None:
        return None
    days = (due - today).days
    if days < 0:
        return Due("Yesterday" if days == -1 else f"{-days} days late", "late")
    if days == 0:
        return Due("Today", "today")
    if days == 1:
        return Due("Tomorrow", "soon")
    if days < 7:
        return Due(due.strftime("%A"), "soon")
    return Due(f"{due.day} {due.strftime('%b')}" + (f" {due.year}" if due.year != today.year else ""), "")
