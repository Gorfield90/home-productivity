import pytest
from sqlalchemy import select

from app.models import Item, Recipe
from app.recipes import parse_line
from tests.conftest import add, items

BRAS = {
    "title": "Bacalhau à Brás", "servings": "4", "minutes": "40", "tags": "Fish, Weeknight",
    "ingredients": "400 g bacalhau\n2 onions, sliced\n6 eggs\n- 1 tbsp olive oil\nsal q.b.\n\n",
    "steps": "Fry the onions.\n\nAdd the fish.\nStir in the eggs.", "notes": "", "source_url": "",
}


@pytest.mark.parametrize("line, expected", [
    ("2 onions, chopped", ("Onions", "2")),
    ("1 tbsp olive oil", ("Olive oil", "1 tbsp")),
    ("200g flour", ("Flour", "200 g")),
    ("1 1/2 cups milk", ("Milk", "1 1/2 cups")),
    ("3 cloves garlic (crushed)", ("Garlic", "3 cloves")),
    ("2 colheres de sopa de azeite", ("Azeite", "2 colheres de sopa")),
    ("500 g de bacalhau", ("Bacalhau", "500 g")),
    ("2 dentes de alho", ("Alho", "2 dentes")),
    ("sal q.b.", ("Sal", "to taste")),
    ("Salt and pepper", ("Salt and pepper", "")),
])
def test_parse_line(line, expected):
    assert parse_line(line) == expected


def create(client, **overrides):
    r = client.post("/recipes", data={**BRAS, **overrides}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def recipe(client, rid):
    with client.app.state.db.sessionmaker() as s:
        return s.get(Recipe, rid)


def test_create_and_view(client):
    rid = create(client)
    r = recipe(client, rid)
    assert [i.line for i in r.ingredients] == ["400 g bacalhau", "2 onions, sliced", "6 eggs", "1 tbsp olive oil", "sal q.b."]
    assert r.tags == "fish, weeknight" and r.step_list == ["Fry the onions.", "Add the fish.", "Stir in the eggs."]
    page = client.get(f"/recipes/{rid}").text
    assert "Bacalhau à Brás" in page and "Serves 4" in page and "<li>Add the fish.</li>" in page


@pytest.mark.parametrize("field, value, message", [
    ("title", " ", "needs a name"), ("servings", "lots", "Servings must be"), ("minutes", "0", "Time must be"),
    ("source_url", "javascript:alert(1)", "must start with http"),
])
def test_form_errors(client, field, value, message):
    r = client.post("/recipes", data={**BRAS, field: value})
    assert r.status_code == 422 and message in r.text
    with client.app.state.db.sessionmaker() as s:
        assert s.scalars(select(Recipe)).all() == []


def test_edit_replaces_ingredients(client):
    rid = create(client)
    r = client.post(f"/recipes/{rid}/edit", data={**BRAS, "title": "Brás", "ingredients": "1 kg potatoes"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert [(i.name, i.quantity) for i in recipe(client, rid).ingredients] == [("Potatoes", "1 kg")]
    assert client.post(f"/recipes/{rid}/edit", data={**BRAS, "servings": "x"}).status_code == 422
    assert recipe(client, rid).title == "Brás"


def test_search_and_tags(client):
    create(client)
    create(client, title="Caldo verde", tags="soup", ingredients="1 couve\n4 potatoes")
    names = lambda url: [t for t in ("Bacalhau à Brás", "Caldo verde") if t in client.get(url).text.split("recipe-grid")[-1]]
    assert names("/recipes") == ["Bacalhau à Brás", "Caldo verde"]
    assert names("/recipes?q=couve") == ["Caldo verde"]       # ingredient
    assert names("/recipes?q=weeknight") == ["Bacalhau à Brás"]  # tag
    assert names("/recipes?tag=soup") == ["Caldo verde"]
    assert "No recipe matches" in client.get("/recipes?q=pizza").text


def test_ticked_ingredients_go_to_the_shopping_list(client):
    rid = create(client)
    ings = {i.name: i.id for i in recipe(client, rid).ingredients}
    add(client, "2 onions")  # already on the list
    r = client.post(f"/recipes/{rid}/shopping", data={"ingredient": [ings["Bacalhau"], ings["Onions"], ings["Sal"]]},
                    follow_redirects=False)
    assert r.headers["location"] == f"/recipes/{rid}?added=3"
    on_list = items(client)
    assert on_list["Bacalhau"].quantity == "400 g" and on_list["Bacalhau"].note == "For Bacalhau à Brás"
    assert on_list["Onions"].quantity == "2 + 2" and on_list["Onions"].note == "For Bacalhau à Brás"
    assert on_list["Sal"].quantity == ""          # "to taste" isn't something to buy
    assert "Eggs" not in on_list                  # not ticked
    assert "3 ingredients added" in client.get(r.headers["location"]).text


def test_delete_recipe(client):
    rid = create(client)
    client.post(f"/recipes/{rid}/delete")
    assert client.get(f"/recipes/{rid}").status_code == 404
    with client.app.state.db.sessionmaker() as s:
        assert s.scalars(select(Item)).all() == []
