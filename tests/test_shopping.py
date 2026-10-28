import pytest

from app.shopping import parse_entry
from tests.conftest import FETCH, add, items


@pytest.mark.parametrize("text, expected", [
    ("milk", ("milk", "")),
    ("2 milk", ("milk", "2")),
    ("6x eggs", ("eggs", "6")),
    ("1.5 kg potatoes", ("potatoes", "1.5kg")),
    ("500g flour", ("flour", "500g")),
    ("10 un yogurt", ("yogurt", "10 un")),
    ("milk x2", ("milk", "2")),
    ("2 lemons", ("lemons", "2")),
    ("7up", ("7up", "")),
    ("  olive   oil ", ("olive oil", "")),
])
def test_parse_entry(text, expected):
    assert parse_entry(text) == expected


def test_add_shows_on_the_list(client):
    r = add(client, "2 milk")
    assert r.status_code == 303 and r.headers["location"] == "/shopping"
    page = client.get("/shopping").text
    assert "Milk" in page and '<span class="qty">2</span>' in page
    assert items(client)["Milk"].quantity == "2"


def test_empty_entry_is_refused(client):
    r = add(client, "   ")
    assert r.status_code == 422
    assert "Type what you need" in r.text
    assert items(client) == {}


def test_same_item_twice_updates_instead_of_duplicating(client):
    add(client, "milk")
    r = add(client, "3 Milk", headers=FETCH)
    assert r.status_code == 200
    assert "already on the list" in r.text
    assert list(items(client)) == ["Milk"]
    assert items(client)["Milk"].quantity == "3"


def test_fetch_gets_the_list_fragment(client):
    r = add(client, "bread", headers=FETCH)
    assert r.status_code == 200
    assert r.text.lstrip().startswith('<div class="list-inner"')
    assert "<html" not in r.text


def test_both_people_share_one_list(client, maguy):
    add(client, "bread")
    add(maguy, "apples")
    page = maguy.get("/shopping").text
    assert "Bread" in page and "Apples" in page
    assert 'title="Added by Pedro"' in page and 'title="Added by Maguy"' in page


def test_items_are_grouped_by_aisle_in_shop_order(client):
    cats = {c.name: c.id for c in _categories(client)}
    add(client, "cheese", category_id=cats["Dairy & eggs"])
    add(client, "carrots", category_id=cats["Fruit & veg"])
    add(client, "mystery")
    page = client.get("/shopping").text
    assert page.index("Fruit &amp; veg") < page.index("Dairy &amp; eggs") < page.index("Other")
    assert page.index("Carrots") < page.index("Cheese") < page.index("Mystery")


def test_tick_untick_and_finish_shopping(client, maguy):
    add(client, "bread")
    bread = items(client)["Bread"]
    maguy.post(f"/shopping/items/{bread.id}/toggle")
    assert items(client)["Bread"].checked_at is not None
    page = client.get("/shopping").text
    assert "In the trolley" in page and 'title="Ticked by Maguy"' in page

    client.post(f"/shopping/items/{bread.id}/toggle")
    assert items(client)["Bread"].checked_at is None

    client.post(f"/shopping/items/{bread.id}/toggle")
    client.post("/shopping/clear")
    assert items(client) == {}
    page = client.get("/shopping").text
    assert "Recently bought" in page and "Add Bread again" in page


def test_clear_leaves_unticked_items(client):
    add(client, "bread")
    add(client, "milk")
    client.post(f"/shopping/items/{items(client)['Bread'].id}/toggle")
    client.post("/shopping/clear")
    assert list(items(client)) == ["Milk"]


def test_readding_remembers_the_aisle(client):
    cats = {c.name: c.id for c in _categories(client)}
    add(client, "yogurt", category_id=cats["Dairy & eggs"])
    _buy_all(client)
    add(client, "Yogurt")
    assert items(client)["Yogurt"].category_id == cats["Dairy & eggs"]


def test_recently_bought_skips_what_is_on_the_list(client):
    add(client, "bread")
    _buy_all(client)
    assert "Add Bread again" in client.get("/shopping").text
    add(client, "bread")
    assert "Add Bread again" not in client.get("/shopping").text


def test_forget_removes_a_suggestion(client):
    add(client, "bread")
    _buy_all(client)
    client.post("/shopping/forget", data={"name": "bread"})
    assert "Recently bought" not in client.get("/shopping").text


def test_edit_and_delete_an_item(client):
    add(client, "bread")
    bread = items(client)["Bread"]
    assert client.get(f"/shopping/items/{bread.id}").status_code == 200
    r = client.post(f"/shopping/items/{bread.id}", data={"name": "sourdough", "quantity": "1",
                                                          "note": "from the market", "category_id": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    assert items(client)["Sourdough"].note == "from the market"
    assert client.post(f"/shopping/items/{bread.id}", data={"name": " "}).status_code == 422
    client.post(f"/shopping/items/{bread.id}/delete")
    assert items(client) == {}


def test_unknown_item_is_404(client):
    assert client.post("/shopping/items/999/toggle").status_code == 404


def test_revision_moves_on_every_change(client, maguy):
    before = maguy.get("/rev").json()["rev"]
    add(client, "bread")
    after = maguy.get("/rev").json()["rev"]
    assert after != before
    assert f'data-rev="{after}"' in maguy.get("/shopping/list").text


# ---- aisles ----

def test_new_household_gets_default_aisles(client):
    page = client.get("/shopping/categories").text
    assert "Fruit &amp; veg" in page and "Household" in page


def test_add_move_rename_delete_aisle(client):
    client.post("/shopping/categories", data={"name": "Baby", "icon": "🍼"})
    cats = _categories(client)
    baby = cats[-1]
    assert baby.name == "Baby"

    client.post(f"/shopping/categories/{baby.id}/move", data={"dir": "up"})
    assert [c.name for c in _categories(client)][-2] == "Baby"

    client.post(f"/shopping/categories/{baby.id}", data={"name": "Kids", "icon": "🧸"})
    assert any(c.name == "Kids" for c in _categories(client))

    add(client, "nappies", category_id=baby.id)
    client.post(f"/shopping/categories/{baby.id}/delete")
    assert all(c.id != baby.id for c in _categories(client))
    assert items(client)["Nappies"].category_id is None  # still on the list, under Other


def test_aisle_needs_a_name(client):
    assert client.post("/shopping/categories", data={"name": "  "}).status_code == 422


# ---- helpers ----

def _categories(client):
    from app.shopping import categories

    with client.app.state.db.sessionmaker() as s:
        return categories(s)


def _buy_all(client):
    for item in items(client).values():
        client.post(f"/shopping/items/{item.id}/toggle")
    client.post("/shopping/clear")


@pytest.mark.parametrize("text, aisle", [
    ("Bananas", "Fruit & veg"), ("leite", "Dairy & eggs"), ("Kitchen roll", "Household"),
    ("Pasta de dentes", "Personal care"), ("Orange juice", "Drinks"), ("cat food", None),
])
def test_new_products_get_a_first_guess_at_the_aisle(client, text, aisle):
    names = {c.id: c.name for c in _categories(client)}
    add(client, text)
    (item,) = items(client).values()
    assert names.get(item.category_id) == aisle


def test_the_last_aisle_beats_the_guess(client):
    cats = {c.name: c.id for c in _categories(client)}
    add(client, "milk", category_id=cats["Drinks"])
    _buy_all(client)
    add(client, "milk")
    assert items(client)["Milk"].category_id == cats["Drinks"]


def test_tick_fragment_says_who_ticked(client):
    add(client, "bread")
    r = client.post(f"/shopping/items/{items(client)['Bread'].id}/toggle", headers=FETCH)
    assert 'title="Ticked by Pedro"' in r.text
