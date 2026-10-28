from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.config import Settings
from app.main import create_app


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_home_goes_to_the_shopping_list(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/shopping"


def test_no_header_is_401(app):
    with TestClient(app) as c:
        assert c.get("/shopping").status_code == 401


def test_unknown_user_is_403(app):
    with TestClient(app, headers={"X-authentik-username": "stranger"}) as c:
        assert c.get("/shopping").status_code == 403


def test_dev_user(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/t.db", users=[("pedro", "Pedro")], dev_user="pedro"))
    with TestClient(app) as c:
        assert c.get("/shopping").status_code == 200


def test_pages_render(client):
    for url in ("/shopping", "/shopping/categories", "/static/manifest.webmanifest", "/static/live.js", "/todos", "/recipes", "/recipes/new", "/meals", "/meals/shopping", "/watch"):
        assert client.get(url).status_code == 200, url


def test_tab_title_is_always_home(client):
    for url in ("/shopping", "/todos", "/recipes", "/recipes/new", "/meals", "/watch", "/shopping/categories"):
        assert "<title>Home</title>" in client.get(url).text, url


def test_food_pages_sit_together_in_the_menu(client):
    page = client.get("/shopping").text
    side, tabbar = page.split('<aside')[1].split('</aside>')[0], page.split('class="tabbar"')[1]
    for nav, order in ((side, ["/shopping", "/recipes", "/meals", "/todos", "/watch", "/plants"]),
                       (tabbar, ["/shopping", "/recipes", "/meals", "/todos", "/more"])):
        assert sorted(order, key=lambda u: nav.index(f'href="{u}"')) == order


def test_phone_bar_keeps_the_rest_behind_more(client):
    """Four everyday pages and More on the phone; More lights up on the pages it holds."""
    tabbar = client.get("/shopping").text.split('class="tabbar"')[1]
    assert tabbar.count("<a ") == 5 and 'href="/watch"' not in tabbar
    for url in ("/more", "/watch", "/plants"):
        more = client.get(url).text.split('class="tabbar"')[1].split('href="/more"')[1].split("</a>")[0]
        assert 'aria-current="page"' in more
    page = client.get("/more").text
    assert 'href="/plants"' in page and 'href="/watch"' in page


def test_migrations_match_the_models(client, tmp_path):
    """The app migrated test.db at startup; nothing in the models should be missing from it."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.db import Base

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
