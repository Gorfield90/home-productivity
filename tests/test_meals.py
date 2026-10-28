from datetime import date

from sqlalchemy import select

from app.meals import parse_week
from app.models import Meal
from tests.conftest import FETCH, TODAY, items
from tests.test_recipes import create, recipe

MONDAY = "2026-10-26"  # the week of TODAY (Wednesday 28 Oct)


def plan(client, day, slot="dinner", recipe_id="", text="", headers=None):
    return client.post("/meals", data={"day": day, "slot": slot, "recipe_id": recipe_id, "text": text},
                       headers=headers, follow_redirects=False)


def meals(client):
    with client.app.state.db.sessionmaker() as s:
        return list(s.scalars(select(Meal).order_by(Meal.id)))


def test_parse_week():
    assert parse_week(None, TODAY) == date(2026, 10, 26)
    assert parse_week("2026-11-05", TODAY) == date(2026, 11, 2)
    assert parse_week("rubbish", TODAY) == date(2026, 10, 26)


def test_week_page_shows_monday_to_sunday_and_today(client):
    page = client.get("/meals").text
    assert "Monday" in page and "Sunday" in page and "26 Oct – 1 Nov" in page
    assert 'class="card day-card today"' in page
    assert "/meals?week=2026-10-19" in page and "/meals?week=2026-11-02" in page


def test_plan_recipe_and_free_text(client):
    rid = create(client)
    r = plan(client, "2026-10-29", recipe_id=str(rid))
    assert r.status_code == 303 and r.headers["location"] == f"/meals?week={MONDAY}"
    plan(client, "2026-10-29", slot="lunch", text="  leftovers ")
    page = client.get("/meals").text
    assert f'href="/recipes/{rid}">Bacalhau à Brás' in page and "<span>leftovers</span>" in page
    assert [(m.slot, m.label) for m in meals(client)] == [("dinner", "Bacalhau à Brás"), ("lunch", "leftovers")]


def test_plan_needs_something(client):
    r = plan(client, "2026-10-29", headers=FETCH)
    assert r.status_code == 422 and "Pick a recipe or type" in r.text
    assert plan(client, "2026-10-29", slot="brunch", text="x").status_code == 422
    assert plan(client, "nope", text="x").status_code == 422
    assert meals(client) == []


def test_fetch_returns_the_week_fragment(client):
    r = plan(client, "2026-11-04", text="pizza", headers=FETCH)
    assert r.status_code == 200 and 'class="week"' in r.text and "<html" not in r.text
    assert "pizza" in client.get("/meals/fragment?week=2026-11-02").text


def test_remove_meal(client):
    plan(client, "2026-10-29", text="soup")
    client.post(f"/meals/{meals(client)[0].id}/delete")
    assert meals(client) == []
    assert client.post("/meals/999/delete").status_code == 404


def test_deleted_recipe_keeps_its_name_on_the_plan(client):
    rid = create(client)
    plan(client, "2026-10-29", recipe_id=str(rid))
    client.post(f"/recipes/{rid}/delete")
    (m,) = meals(client)
    assert m.recipe_id is None and m.label == "Bacalhau à Brás"


def test_shop_for_the_week(client):
    bras = create(client)
    soup = create(client, title="Caldo verde", ingredients="1 couve\n4 potatoes")
    plan(client, "2026-10-27", recipe_id=str(bras))
    plan(client, "2026-10-30", recipe_id=str(bras), slot="lunch")
    plan(client, "2026-10-31", recipe_id=str(soup))
    plan(client, "2026-11-03", recipe_id=str(soup))  # next week: not part of this shop
    page = client.get(f"/meals/shopping?week={MONDAY}").text
    assert page.count("Bacalhau à Brás</a>") == 1 and "Tue dinner, Fri lunch" in page and "Sat dinner" in page

    ids = [i.id for i in recipe(client, bras).ingredients if i.name in ("Bacalhau", "Eggs")]
    ids += [i.id for i in recipe(client, soup).ingredients]
    r = client.post("/meals/shopping", data={"week": MONDAY, "ingredient": ids}, follow_redirects=False)
    assert r.headers["location"] == f"/meals/shopping?week={MONDAY}&added=4"
    assert set(items(client)) == {"Bacalhau", "Eggs", "Couve", "Potatoes"}


def test_shop_page_without_recipes(client):
    plan(client, "2026-10-29", text="eat out")
    assert "No recipes are planned" in client.get("/meals/shopping").text



