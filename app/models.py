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


class Recipe(Base):
    """A recipe, shared by the household. Steps are plain text, one per line."""

    __tablename__ = "recipe"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    servings: Mapped[int | None] = mapped_column(Integer)
    minutes: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(String(500), default="", server_default="")
    tags: Mapped[str] = mapped_column(String(200), default="", server_default="")
    steps: Mapped[str] = mapped_column(Text, default="", server_default="")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    created_by: Mapped[User] = relationship(lazy="joined")
    ingredients: Mapped[list["Ingredient"]] = relationship(
        order_by="Ingredient.position", cascade="all, delete-orphan", lazy="selectin")

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    @property
    def step_list(self) -> list[str]:
        return [s.strip() for s in self.steps.splitlines() if s.strip()]


class Ingredient(Base):
    """One ingredient line as typed ("2 onions"), split into name and quantity for the shopping list."""

    __tablename__ = "ingredient"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipe.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    line: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[str] = mapped_column(String(40), default="", server_default="")


# The meals a day can have on the plan, in the order they are eaten.
SLOTS = {"lunch": "Lunch", "dinner": "Dinner"}


class Meal(Base):
    """One dish planned for a day's lunch or dinner: a recipe, or just a few words ("leftovers")."""

    __tablename__ = "meal"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    slot: Mapped[str] = mapped_column(String(10))
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipe.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(String(120), default="", server_default="")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    recipe: Mapped[Recipe | None] = relationship(lazy="joined")
    created_by: Mapped[User] = relationship(lazy="joined")

    @property
    def label(self) -> str:
        return self.recipe.title if self.recipe else self.text


# What a show on the watch list can be.
KINDS = {"series": "Series", "movie": "Movie"}


class Show(Base):
    """A series or movie someone would like to watch next.

    In the pool while picked_at is empty; the one picked (at most one) has picked_at set; once
    watched it gets watched_at and lives on as history.
    """

    __tablename__ = "show"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10), default="series", server_default="series")
    added_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    picked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    picked_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    watched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    added_by: Mapped[User] = relationship(foreign_keys=[added_by_id], lazy="joined")
    picked_by: Mapped[User | None] = relationship(foreign_keys=[picked_by_id], lazy="joined")
