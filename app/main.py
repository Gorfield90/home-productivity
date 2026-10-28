"""FastAPI application: server-rendered pages and plain HTML forms.

Forms work without JavaScript (post, then redirect). The shopping page's script sends the same
forms with fetch and an X-Requested-With header instead; those requests get the list fragment
back, so the page updates in place and stays where it was.
"""

from contextlib import asynccontextmanager
from datetime import timezone
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
from .models import Category, Item, User
from . import shopping

BASE_DIR = Path(__file__).parent


def ensure_seed_data(session: Session, settings: Settings) -> None:
    for username, display in settings.users:
        if not session.scalar(select(User).where(User.username == username)):
            session.add(User(username=username, display_name=display))
    shopping.ensure_categories(session)
    session.commit()


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def create_app(settings: Settings | None = None) -> FastAPI:
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
    templates.env.globals.update(version=settings.version)

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
        # The shopping list is the only feature so far; to-dos, recipes and meals will join it here.
        return back("/shopping")

    # ---- shopping list ----

    @app.get("/shopping")
    def shopping_page(request: Request, session: SessionDep, user: UserDep):
        return render(request, "shopping.html", user, **list_context(session))

    @app.get("/shopping/list")
    def shopping_fragment(request: Request, session: SessionDep, user: UserDep):
        return render(request, "_shopping_list.html", user, **list_context(session))

    @app.get("/shopping/rev")
    def shopping_rev(session: SessionDep, user: UserDep):
        """Polled by open pages: when the number changes, someone changed the list."""
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
