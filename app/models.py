"""Data model. Everything is shared between the household's users (rows remember who did what), except personal to-do lists."""

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# The aisles a new household starts with, in a typical walk through the supermarket.
DEFAULT_CATEGORIES = [
    ("Fruit & veg", "🥦"), ("Bakery", "🥖"), ("Meat & fish", "🍗"), ("Dairy & eggs", "🧀"),
    ("Pantry", "🥫"), ("Frozen", "🧊"), ("Drinks", "🧃"), ("Household", "🧻"), ("Personal care", "🧴"),
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))


class Revision(Base):
    """One row whose counter goes up on every change, so open pages know when to refresh."""

    __tablename__ = "revision"

    id: Mapped[int] = mapped_column(primary_key=True)
    n: Mapped[int] = mapped_column(Integer, default=0)


class Category(Base):
    """A shopping aisle. The list is sorted by position, so it follows the walk through the shop."""

    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    icon: Mapped[str] = mapped_column(String(16), default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, default=0)


class Item(Base):
    """A shopping list line.

    On the list while bought_at is empty; ticked (in the trolley) once checked_at is set; once the
    ticked items are cleared it gets bought_at and lives on as history, which feeds "recently bought"
    and remembers each product's category.
    """

    __tablename__ = "item"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[str] = mapped_column(String(40), default="", server_default="")
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id", ondelete="SET NULL"))
    added_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    bought_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    category: Mapped[Category | None] = relationship(lazy="joined")
    added_by: Mapped[User] = relationship(foreign_keys=[added_by_id], lazy="joined")
    checked_by: Mapped[User | None] = relationship(foreign_keys=[checked_by_id], lazy="joined")


class TodoList(Base):
    """A to-do list. Shared with everyone unless it has an owner, then only the owner sees it."""

    __tablename__ = "todo_list"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    icon: Mapped[str] = mapped_column(String(16), default="", server_default="")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    owner: Mapped[User | None] = relationship(lazy="joined")

    @property
    def shared(self) -> bool:
        return self.owner_id is None


class Todo(Base):
    """A task on a list. Open while done_at is empty."""

    __tablename__ = "todo"

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("todo_list.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    due: Mapped[date | None] = mapped_column(Date)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))

    todo_list: Mapped[TodoList] = relationship(lazy="joined")
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id], lazy="joined")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id], lazy="joined")
    done_by: Mapped[User | None] = relationship(foreign_keys=[done_by_id], lazy="joined")
