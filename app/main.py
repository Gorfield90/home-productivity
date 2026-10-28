"""FastAPI application: server-rendered pages and plain HTML forms.

Forms work without JavaScript (post, then redirect). The shopping page's script sends the same
forms with fetch and an X-Requested-With header instead; those requests get the list fragment
back, so the page updates in place and stays where it was.
"""

from contextlib import asynccontextmanager
from datetime import date, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import Database
from .migrate import upgrade
from .models import KINDS, SLOTS, Category, Item, Meal, Recipe, Show, User
from . import meals, recipes, shopping, todos, watch

BASE_DIR = Path(__file__).parent


def ensure_seed_data(session: Session, settings: Settings) -> None:
    for username, display in settings.users:
        if not session.scalar(select(User).where(User.username == username)):
            session.add(User(username=username, display_name=display))
    shopping.ensure_categories(session)
    todos.ensure_lists(session)
    session.commit()


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def create_app(settings: Settings | None = None, today=date.today) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        upgrade(db)
        with db.sessionmaker() as s:
            ensure_seed_data(s, settings)
        yield

    app = FastAPI(title="Home", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.db = db
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    templates.env.globals.update(version=settings.version, due_label=lambda d: todos.due_label(d, today()), SLOTS=SLOTS,
                                 KINDS=KINDS)

    def get_session():
        yield from db.session()

    SessionDep = Annotated[Session, Depends(get_session)]

    def current_user(request: Request, session: SessionDep) -> User:
        username = request.headers.get(settings.auth_header) or settings.dev_user
        if not username:
            raise HTTPException(401, "not authenticated")
        user = session.scalar(select(User).where(User.username == username))
        if not user:
            raise HTTPException(403, f"user {username!r} is not allowed to use this app")
        return user

    UserDep = Annotated[User, Depends(current_user)]

    def user_colors(session: Session) -> dict[int, str]:
        """Avatar colour per person: the first user gets u1, the second u2."""
        ids = session.scalars(select(User.id).order_by(User.id)).all()
        return {uid: f"u{i % 2 + 1}" for i, uid in enumerate(ids)}

    def render(request: Request, name: str, user: User, status_code: int = 200, **ctx):
        return templates.TemplateResponse(request, name, {"user": user, **ctx}, status_code=status_code)

    def back(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    def wants_fragment(request: Request) -> bool:
        return request.headers.get("X-Requested-With") == "fetch"

    def list_context(session: Session, **extra) -> dict:
        items = shopping.on_list(session)
        cats = shopping.categories(session)
        return {
            "groups": shopping.grouped([i for i in items if i.checked_at is None], cats),
            "ticked": sorted((i for i in items if i.checked_at is not None),
                             key=lambda i: i.checked_at.replace(tzinfo=i.checked_at.tzinfo or timezone.utc),
                             reverse=True),
            "recent": shopping.recently_bought(session),
            "categories": cats,
            "colors": user_colors(session),
            "rev": shopping.revision(session),
            **extra,
        }

    def list_or_back(request: Request, session: Session, user: User, error: str = "", added: str = ""):
        """After a change: the list fragment for the page's script, a redirect for a plain form post."""
        if wants_fragment(request):
            return render(request, "_shopping_list.html", user, status_code=422 if error else 200,
                          **list_context(session, error=error, added=added))
        if error:
            return render(request, "shopping.html", user, status_code=422, **list_context(session, error=error))
        return back("/shopping")

    def own_item(session: Session, item_id: int) -> Item:
        item = session.get(Item, item_id)
        if item is None:
            raise HTTPException(404)
        return item

    def own_category(session: Session, cat_id: int) -> Category:
        cat = session.get(Category, cat_id)
        if cat is None:
            raise HTTPException(404)
        return cat

    # ---- health ----

    @app.get("/healthz")
    def healthz(session: SessionDep):
        session.execute(select(1))
        return {"status": "ok", "version": settings.version}

    @app.get("/")
    def home(user: UserDep):
        return back("/shopping")

    # ---- shopping list ----

    @app.get("/shopping")
    def shopping_page(request: Request, session: SessionDep, user: UserDep):
        return render(request, "shopping.html", user, **list_context(session))

    @app.get("/shopping/list")
    def shopping_fragment(request: Request, session: SessionDep, user: UserDep):
        return render(request, "_shopping_list.html", user, **list_context(session))

    @app.get("/rev")
    def rev(session: SessionDep, user: UserDep):
        """Polled by open pages: when the number changes, someone changed something."""
        return {"rev": shopping.revision(session)}

    @app.post("/shopping/items")
    async def add_item(request: Request, session: SessionDep, user: UserDep):
        form = await request.form()
        try:
            item, created = shopping.add_item(session, user, str(form.get("text", "")),
                                              category_id=_int_or_none(form.get("category_id")),
                                              quantity=str(form.get("quantity", "")))
        except shopping.FormError as e:
            return list_or_back(request, session, user, error=str(e))
        session.commit()
        return list_or_back(request, session, user, added=item.name if created else f"{item.name} was already on the list")

    @app.post("/shopping/items/{item_id}/toggle")
    def toggle_item(item_id: int, request: Request, session: SessionDep, user: UserDep):
        item = own_item(session, item_id)
        if item.bought_at is None:
            shopping.toggle(session, user, item)
            session.commit()
        return list_or_back(request, session, user)

    @app.post("/shopping/clear")
    def clear_ticked(request: Request, session: SessionDep, user: UserDep):
        shopping.clear_ticked(session)
        session.commit()
        return list_or_back(request, session, user)

    @app.post("/shopping/forget")
    async def forget(request: Request, session: SessionDep, user: UserDep):
        form = await request.form()
        shopping.forget(session, str(form.get("name", "")))
        session.commit()
        return list_or_back(request, session, user)

    @app.get("/shopping/items/{item_id}")
    def edit_item_form(item_id: int, request: Request, session: SessionDep, user: UserDep):
        item = own_item(session, item_id)
        return render(request, "item_form.html", user, item=item, categories=shopping.categories(session),
                      form={"name": item.name, "quantity": item.quantity, "note": item.note,
                            "category_id": item.category_id})

    @app.post("/shopping/items/{item_id}")
    async def edit_item(item_id: int, request: Request, session: SessionDep, user: UserDep):
        item = own_item(session, item_id)
        form = await request.form()
        try:
            item.name = shopping.clean_name(str(form.get("name", "")))
        except shopping.FormError as e:
            return render(request, "item_form.html", user, status_code=422, item=item, error=str(e),
                          categories=shopping.categories(session), form=dict(form))
        item.quantity = str(form.get("quantity", "")).strip()[:40]
        item.note = str(form.get("note", "")).strip()[:500]
        cat_id = _int_or_none(form.get("category_id"))
        item.category_id = cat_id if cat_id is not None and session.get(Category, cat_id) else None
        shopping.bump(session)
        session.commit()
        return back("/shopping")

    @app.post("/shopping/items/{item_id}/delete")
    def delete_item(item_id: int, request: Request, session: SessionDep, user: UserDep):
        session.delete(own_item(session, item_id))
        shopping.bump(session)
        session.commit()
        return list_or_back(request, session, user)

    # ---- to-do lists ----

    def users(session: Session) -> list[User]:
        return list(session.scalars(select(User).order_by(User.id)))

    def own_list(session: Session, user: User, list_id: int):
        tl = todos.get_list(session, user, list_id)
        if tl is None:
            raise HTTPException(404)
        return tl

    def own_todo(session: Session, user: User, todo_id: int):
        todo = todos.get_todo(session, user, todo_id)
        if todo is None:
            raise HTTPException(404)
        return todo

    def todo_context(session: Session, user: User, tl, **extra) -> dict:
        done, done_total = todos.done_todos(session, tl)
        return {"tl": tl, "open": todos.open_todos(session, tl), "done": done, "done_total": done_total,
                "users": users(session), "colors": user_colors(session), "rev": shopping.revision(session),
                **extra}

    def todo_list_or_back(request: Request, session: Session, user: User, tl, error: str = ""):
        if wants_fragment(request):
            return render(request, "_todo_list.html", user, status_code=422 if error else 200,
                          **todo_context(session, user, tl, error=error))
        if error:
            return render(request, "todo_list.html", user, status_code=422, **todo_context(session, user, tl, error=error))
        return back(f"/todos/{tl.id}")

    def overview_context(session: Session, user: User) -> dict:
        return {"lists": todos.lists(session, user), "counts": todos.open_counts(session, user),
                "soon": todos.coming_up(session, user, today()), "colors": user_colors(session),
                "rev": shopping.revision(session)}

    @app.get("/todos")
    def todos_page(request: Request, session: SessionDep, user: UserDep):
        return render(request, "todos.html", user, **overview_context(session, user))

    @app.get("/todos/fragment")
    def todos_fragment(request: Request, session: SessionDep, user: UserDep):
        return render(request, "_todos_overview.html", user, **overview_context(session, user))

    @app.get("/todos/new")
    def new_list_form(request: Request, user: UserDep):
        return render(request, "todo_list_form.html", user, tl=None, form={})

    @app.post("/todos")
    async def add_list(request: Request, session: SessionDep, user: UserDep):
        form = await request.form()
        try:
            tl = todos.add_list(session, user, str(form.get("name", "")), str(form.get("icon", "")),
                                personal=form.get("visibility") == "personal")
        except shopping.FormError as e:
            return render(request, "todo_list_form.html", user, status_code=422, tl=None, error=str(e), form=dict(form))
        session.commit()
        return back(f"/todos/{tl.id}")

    @app.get("/todos/{list_id}")
    def todo_list_page(list_id: int, request: Request, session: SessionDep, user: UserDep):
        tl = own_list(session, user, list_id)
        return render(request, "todo_list.html", user, **todo_context(session, user, tl))

    @app.get("/todos/{list_id}/fragment")
    def todo_list_fragment(list_id: int, request: Request, session: SessionDep, user: UserDep):
        tl = own_list(session, user, list_id)
        return render(request, "_todo_list.html", user, **todo_context(session, user, tl))

    @app.post("/todos/{list_id}/items")
    async def add_todo(list_id: int, request: Request, session: SessionDep, user: UserDep):
        tl = own_list(session, user, list_id)
        form = await request.form()
        try:
            todos.add_todo(session, user, tl, str(form.get("title", "")), str(form.get("due", "")),
                           form.get("assignee_id"))
        except shopping.FormError as e:
            return todo_list_or_back(request, session, user, tl, error=str(e))
        session.commit()
        return todo_list_or_back(request, session, user, tl)

    @app.post("/todos/{list_id}/clear")
    def clear_done(list_id: int, request: Request, session: SessionDep, user: UserDep):
        tl = own_list(session, user, list_id)
        todos.clear_done(session, tl)
        session.commit()
        return todo_list_or_back(request, session, user, tl)

    @app.get("/todos/{list_id}/edit")
    def edit_list_form(list_id: int, request: Request, session: SessionDep, user: UserDep):
        tl = own_list(session, user, list_id)
        return render(request, "todo_list_form.html", user, tl=tl,
                      form={"name": tl.name, "icon": tl.icon, "visibility": "shared" if tl.shared else "personal"})

    @app.post("/todos/{list_id}/edit")
    async def edit_list(list_id: int, request: Request, session: SessionDep, user: UserDep):
        tl = own_list(session, user, list_id)
        form = await request.form()
        try:
            tl.name, tl.icon = todos.clean_list(str(form.get("name", "")), str(form.get("icon", "")))
        except shopping.FormError as e:
            session.rollback()
            return render(request, "todo_list_form.html", user, status_code=422, tl=tl, error=str(e), form=dict(form))
        todos.set_personal(session, user, tl, form.get("visibility") == "personal")
        shopping.bump(session)
        session.commit()
        return back(f"/todos/{tl.id}")

    @app.post("/todos/{list_id}/delete")
    def delete_list(list_id: int, session: SessionDep, user: UserDep):
        session.delete(own_list(session, user, list_id))
        shopping.bump(session)
        session.commit()
        return back("/todos")

    @app.post("/todos/items/{todo_id}/toggle")
    def toggle_todo(todo_id: int, request: Request, session: SessionDep, user: UserDep):
        todo = own_todo(session, user, todo_id)
        todos.toggle(session, user, todo)
        session.commit()
        if request.headers.get("X-From") == "overview" and wants_fragment(request):
            return render(request, "_todos_overview.html", user, **overview_context(session, user))
        return todo_list_or_back(request, session, user, todo.todo_list)

    @app.get("/todos/items/{todo_id}")
    def edit_todo_form(todo_id: int, request: Request, session: SessionDep, user: UserDep):
        todo = own_todo(session, user, todo_id)
        return render(request, "todo_form.html", user, todo=todo, lists=todos.lists(session, user), users=users(session),
                      form={"title": todo.title, "note": todo.note, "due": todo.due.isoformat() if todo.due else "",
                            "assignee_id": todo.assignee_id, "list_id": todo.list_id})

    @app.post("/todos/items/{todo_id}")
    async def edit_todo(todo_id: int, request: Request, session: SessionDep, user: UserDep):
        todo = own_todo(session, user, todo_id)
        form = await request.form()
        target = todos.get_list(session, user, _int_or_none(form.get("list_id")) or todo.list_id) or todo.todo_list
        try:
            title = todos.clean_title(str(form.get("title", "")))
            due = todos.parse_due(str(form.get("due", "")))
        except shopping.FormError as e:
            return render(request, "todo_form.html", user, status_code=422, todo=todo, error=str(e),
                          lists=todos.lists(session, user), users=users(session), form=dict(form))
        todo.title, todo.due = title, due
        todo.note = str(form.get("note", "")).strip()[:1000]
        todo.list_id, todo.todo_list = target.id, target
        todo.assignee_id = todos.pick_assignee(session, target, form.get("assignee_id"))
        shopping.bump(session)
        session.commit()
        return back(f"/todos/{target.id}")

    @app.post("/todos/items/{todo_id}/delete")
    def delete_todo(todo_id: int, request: Request, session: SessionDep, user: UserDep):
        todo = own_todo(session, user, todo_id)
        tl = todo.todo_list
        session.delete(todo)
        shopping.bump(session)
        session.commit()
        return todo_list_or_back(request, session, user, tl)

    # ---- recipes ----

    def own_recipe(session: Session, recipe_id: int) -> Recipe:
        recipe = session.get(Recipe, recipe_id)
        if recipe is None:
            raise HTTPException(404)
        return recipe

    @app.get("/recipes")
    def recipes_page(request: Request, session: SessionDep, user: UserDep, q: str = "", tag: str = ""):
        return render(request, "recipes.html", user, recipes=recipes.search(session, q, tag),
                      tags=recipes.all_tags(session), q=q, tag=tag)

    @app.get("/recipes/new")
    def new_recipe_form(request: Request, user: UserDep):
        return render(request, "recipe_form.html", user, recipe=None, form={})

    @app.post("/recipes")
    async def add_recipe(request: Request, session: SessionDep, user: UserDep):
        form = await request.form()
        recipe = Recipe(created_by=user)
        try:
            recipes.apply_form(recipe, form)
        except shopping.FormError as e:
            return render(request, "recipe_form.html", user, status_code=422, recipe=None, error=str(e), form=dict(form))
        session.add(recipe)
        shopping.bump(session)
        session.commit()
        return back(f"/recipes/{recipe.id}")

    @app.get("/recipes/{recipe_id}")
    def recipe_page(recipe_id: int, request: Request, session: SessionDep, user: UserDep, added: int | None = None):
        return render(request, "recipe.html", user, recipe=own_recipe(session, recipe_id), added=added)

    @app.get("/recipes/{recipe_id}/edit")
    def edit_recipe_form(recipe_id: int, request: Request, session: SessionDep, user: UserDep):
        recipe = own_recipe(session, recipe_id)
        return render(request, "recipe_form.html", user, recipe=recipe, form=recipes.form_values(recipe))

    @app.post("/recipes/{recipe_id}/edit")
    async def edit_recipe(recipe_id: int, request: Request, session: SessionDep, user: UserDep):
        recipe = own_recipe(session, recipe_id)
        form = await request.form()
        try:
            recipes.apply_form(recipe, form)
        except shopping.FormError as e:
            session.rollback()
            return render(request, "recipe_form.html", user, status_code=422, recipe=own_recipe(session, recipe_id),
                          error=str(e), form=dict(form))
        shopping.bump(session)
        session.commit()
        return back(f"/recipes/{recipe.id}")

    @app.post("/recipes/{recipe_id}/delete")
    def delete_recipe(recipe_id: int, session: SessionDep, user: UserDep):
        recipe = own_recipe(session, recipe_id)
        meals.keep_names(session, recipe)
        session.delete(recipe)
        shopping.bump(session)
        session.commit()
        return back("/recipes")

    @app.post("/recipes/{recipe_id}/shopping")
    async def recipe_to_shopping(recipe_id: int, request: Request, session: SessionDep, user: UserDep):
        recipe = own_recipe(session, recipe_id)
        form = await request.form()
        ids = {i for i in (_int_or_none(v) for v in form.getlist("ingredient")) if i is not None}
        added = recipes.to_shopping(session, user, recipe, ids)
        session.commit()
        return back(f"/recipes/{recipe.id}?added={len(added)}")

    # ---- meal planner ----

    def meals_context(session: Session, start, **extra) -> dict:
        return {"start": start, "days": meals.week(session, start), "today": today(),
                "this_week": meals.monday(today()), "prev": start - timedelta(days=7), "next": start + timedelta(days=7),
                "recipes": recipes.search(session), "planned": meals.week_recipes(session, start),
                "colors": user_colors(session), "rev": shopping.revision(session), **extra}

    def meals_or_back(request: Request, session: Session, user: User, start, error: str = ""):
        if wants_fragment(request):
            return render(request, "_meals_week.html", user, status_code=422 if error else 200,
                          **meals_context(session, start, error=error))
        if error:
            return render(request, "meals.html", user, status_code=422, **meals_context(session, start, error=error))
        return back(f"/meals?week={start.isoformat()}")

    @app.get("/meals")
    def meals_page(request: Request, session: SessionDep, user: UserDep, week: str | None = None):
        return render(request, "meals.html", user, **meals_context(session, meals.parse_week(week, today())))

    @app.get("/meals/fragment")
    def meals_fragment(request: Request, session: SessionDep, user: UserDep, week: str | None = None):
        return render(request, "_meals_week.html", user, **meals_context(session, meals.parse_week(week, today())))

    @app.post("/meals")
    async def add_meal(request: Request, session: SessionDep, user: UserDep):
        form = await request.form()
        start = meals.parse_week(str(form.get("day", "")), today())
        try:
            meals.add_meal(session, user, str(form.get("day", "")), str(form.get("slot", "")),
                           form.get("recipe_id"), str(form.get("text", "")))
        except shopping.FormError as e:
            return meals_or_back(request, session, user, start, error=str(e))
        session.commit()
        return meals_or_back(request, session, user, start)

    @app.post("/meals/{meal_id}/delete")
    def delete_meal(meal_id: int, request: Request, session: SessionDep, user: UserDep):
        meal = session.get(Meal, meal_id)
        if meal is None:
            raise HTTPException(404)
        start = meals.monday(meal.day)
        session.delete(meal)
        shopping.bump(session)
        session.commit()
        return meals_or_back(request, session, user, start)

    @app.get("/meals/shopping")
    def week_shopping_page(request: Request, session: SessionDep, user: UserDep, week: str | None = None,
                           added: int | None = None):
        start = meals.parse_week(week, today())
        return render(request, "meals_shopping.html", user, start=start, planned=meals.week_recipes(session, start),
                      added=added)

    @app.post("/meals/shopping")
    async def week_to_shopping(request: Request, session: SessionDep, user: UserDep):
        form = await request.form()
        start = meals.parse_week(str(form.get("week", "")), today())
        ids = {i for i in (_int_or_none(v) for v in form.getlist("ingredient")) if i is not None}
        added = meals.week_to_shopping(session, user, start, ids)
        session.commit()
        return back(f"/meals/shopping?week={start.isoformat()}&added={added}")

    # ---- watch list ----

    def own_show(session: Session, show_id: int) -> Show:
        show = session.get(Show, show_id)
        if show is None:
            raise HTTPException(404)
        return show

    def watch_context(session: Session, **extra) -> dict:
        waiting = watch.pool(session)
        return {"up_next": watch.current(session), "people": watch.by_person(waiting, users(session)), "waiting": len(waiting),
                "watched": watch.watched(session), "colors": user_colors(session), "rev": shopping.revision(session),
                **extra}

    def watch_or_back(request: Request, session: Session, user: User, error: str = "", added: str = ""):
        if wants_fragment(request):
            return render(request, "_watch.html", user, status_code=422 if error else 200,
                          **watch_context(session, error=error, added=added))
        if error:
            return render(request, "watch.html", user, status_code=422, **watch_context(session, error=error))
        return back("/watch")

    @app.get("/watch")
    def watch_page(request: Request, session: SessionDep, user: UserDep):
        return render(request, "watch.html", user, **watch_context(session))

    @app.get("/watch/fragment")
    def watch_fragment(request: Request, session: SessionDep, user: UserDep):
        return render(request, "_watch.html", user, **watch_context(session))

    @app.post("/watch/shows")
    async def add_show(request: Request, session: SessionDep, user: UserDep):
        form = await request.form()
        try:
            show = watch.add_show(session, user, str(form.get("title", "")), str(form.get("kind", "")))
        except shopping.FormError as e:
            return watch_or_back(request, session, user, error=str(e))
        session.commit()
        return watch_or_back(request, session, user, added=f"Added {show.title}")

    @app.post("/watch/shows/{show_id}/delete")
    def delete_show(show_id: int, request: Request, session: SessionDep, user: UserDep):
        session.delete(own_show(session, show_id))
        shopping.bump(session)
        session.commit()
        return watch_or_back(request, session, user)

    @app.post("/watch/pick")
    async def pick_show(request: Request, session: SessionDep, user: UserDep):
        form = await request.form()
        kind = str(form.get("kind", ""))
        try:
            watch.pick(session, user, kind if kind in KINDS else "")
        except shopping.FormError as e:
            return watch_or_back(request, session, user, error=str(e))
        session.commit()
        return watch_or_back(request, session, user)

    @app.post("/watch/shows/{show_id}/watched")
    def show_watched(show_id: int, request: Request, session: SessionDep, user: UserDep):
        show = own_show(session, show_id)
        if show.picked_at is not None and show.watched_at is None:
            watch.mark_watched(session, show)
            session.commit()
        return watch_or_back(request, session, user)

    @app.post("/watch/shows/{show_id}/put-back")
    def put_back_show(show_id: int, request: Request, session: SessionDep, user: UserDep):
        show = own_show(session, show_id)
        if show.picked_at is not None and show.watched_at is None:
            watch.put_back(session, show)
            session.commit()
        return watch_or_back(request, session, user)

    # ---- categories ----

    @app.get("/shopping/categories")
    def categories_page(request: Request, session: SessionDep, user: UserDep):
        return render(request, "categories.html", user, categories=shopping.categories(session))

    @app.post("/shopping/categories")
    async def add_category(request: Request, session: SessionDep, user: UserDep):
        form = await request.form()
        try:
            shopping.add_category(session, str(form.get("name", "")), str(form.get("icon", "")))
        except shopping.FormError as e:
            return render(request, "categories.html", user, status_code=422, error=str(e),
                          categories=shopping.categories(session), form=dict(form))
        session.commit()
        return back("/shopping/categories")

    @app.post("/shopping/categories/{cat_id}")
    async def edit_category(cat_id: int, request: Request, session: SessionDep, user: UserDep):
        cat = own_category(session, cat_id)
        form = await request.form()
        try:
            cat.name, cat.icon = shopping.clean_category(str(form.get("name", "")), str(form.get("icon", "")))
        except shopping.FormError as e:
            session.rollback()
            return render(request, "categories.html", user, status_code=422, error=str(e),
                          categories=shopping.categories(session))
        shopping.bump(session)
        session.commit()
        return back(f"/shopping/categories#cat-{cat.id}")

    @app.post("/shopping/categories/{cat_id}/move")
    async def move_category(cat_id: int, request: Request, session: SessionDep, user: UserDep):
        cat = own_category(session, cat_id)
        form = await request.form()
        shopping.move_category(session, cat, -1 if form.get("dir") == "up" else 1)
        session.commit()
        return back(f"/shopping/categories#cat-{cat.id}")

    @app.post("/shopping/categories/{cat_id}/delete")
    def delete_category(cat_id: int, session: SessionDep, user: UserDep):
        shopping.delete_category(session, own_category(session, cat_id))
        session.commit()
        return back("/shopping/categories")

    return app
