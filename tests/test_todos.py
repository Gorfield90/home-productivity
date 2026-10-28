from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import Todo, TodoList, User
from app.todos import due_label
from tests.conftest import FETCH, TODAY


def _db(client):
    return client.app.state.db.sessionmaker()


def lists(client):
    with _db(client) as s:
        return {tl.name: tl for tl in s.scalars(select(TodoList))}


def tasks(client):
    with _db(client) as s:
        return {t.title: t for t in s.scalars(select(Todo))}


def user_id(client, username):
    with _db(client) as s:
        return s.scalar(select(User.id).where(User.username == username))


def home(client):
    return lists(client)["Home"].id


def add(client, list_id, title, headers=None, **fields):
    return client.post(f"/todos/{list_id}/items", data={"title": title, **fields}, headers=headers,
                       follow_redirects=False)


def new_list(client, name, visibility="shared", icon=""):
    r = client.post("/todos", data={"name": name, "icon": icon, "visibility": visibility}, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


@pytest.mark.parametrize("days, text, kind", [
    (-3, "3 days late", "late"), (-1, "Yesterday", "late"), (0, "Today", "today"), (1, "Tomorrow", "soon"),
    (2, "Friday", "soon"), (9, "6 Nov", ""), (70, "6 Jan 2027", ""),
])
def test_due_label(days, text, kind):
    d = due_label(TODAY + timedelta(days=days), TODAY)
    assert (d.text, d.kind) == (text, kind)


def test_new_household_has_a_shared_home_list(client):
    assert lists(client)["Home"].owner_id is None
    assert "Home" in client.get("/todos").text


def test_add_task_with_due_date_and_assignee(client):
    maguy = user_id(client, "maguy")
    r = add(client, home(client), "call the plumber", due="2026-10-29", assignee_id=str(maguy))
    assert r.status_code == 303
    t = tasks(client)["Call the plumber"]
    assert (t.due, t.assignee_id) == (date(2026, 10, 29), maguy)
    page = client.get(f"/todos/{home(client)}").text
    assert "Tomorrow" in page and 'title="For Maguy"' in page


def test_task_needs_a_title_and_a_real_date(client):
    assert add(client, home(client), "  ").status_code == 422
    r = add(client, home(client), "x", due="not-a-date", headers=FETCH)
    assert r.status_code == 422 and "doesn&#39;t look like a date" in r.text
    assert tasks(client) == {}


def test_open_tasks_soonest_first_undated_last(client):
    lid = home(client)
    add(client, lid, "no date")
    add(client, lid, "later", due="2026-11-20")
    add(client, lid, "sooner", due="2026-10-30")
    page = client.get(f"/todos/{lid}").text
    assert page.index("Sooner") < page.index("Later") < page.index("No date")


def test_done_and_undo_and_clear(client, maguy):
    lid = home(client)
    add(client, lid, "water plants")
    t = tasks(client)["Water plants"]
    r = maguy.post(f"/todos/items/{t.id}/toggle", headers=FETCH)
    assert 'title="Done by Maguy"' in r.text and "Nothing left to do" in r.text
    client.post(f"/todos/items/{t.id}/toggle")
    assert tasks(client)["Water plants"].done_at is None
    client.post(f"/todos/items/{t.id}/toggle")
    client.post(f"/todos/{lid}/clear")
    assert tasks(client) == {}


def test_personal_list_is_private(client, maguy):
    lid = new_list(client, "Work", visibility="personal")
    add(client, lid, "expenses report", due="2026-10-28")
    assert "Work" in client.get("/todos").text
    assert "Work" not in maguy.get("/todos").text
    assert "Expenses report" not in maguy.get("/todos").text  # not in Coming up either
    t = tasks(client)["Expenses report"]
    for url in (f"/todos/{lid}", f"/todos/{lid}/edit", f"/todos/items/{t.id}"):
        assert maguy.get(url).status_code == 404, url
    assert maguy.post(f"/todos/items/{t.id}/toggle").status_code == 404
    assert maguy.post(f"/todos/{lid}/delete").status_code == 404


def test_personal_lists_have_no_assignees(client):
    lid = new_list(client, "Mine", visibility="personal")
    add(client, lid, "thing", assignee_id=str(user_id(client, "maguy")))
    assert tasks(client)["Thing"].assignee_id is None
    assert 'name="assignee_id"' not in client.get(f"/todos/{lid}").text


def test_making_a_list_personal_drops_assignees(client):
    lid = home(client)
    add(client, lid, "thing", assignee_id=str(user_id(client, "maguy")))
    client.post(f"/todos/{lid}/edit", data={"name": "Home", "icon": "🏠", "visibility": "personal"})
    assert lists(client)["Home"].owner_id == user_id(client, "pedro")
    assert tasks(client)["Thing"].assignee_id is None


def test_coming_up_shows_overdue_and_next_week_only(client):
    lid = home(client)
    add(client, lid, "overdue one", due="2026-10-20")
    add(client, lid, "this week", due="2026-11-02")
    add(client, lid, "far away", due="2026-12-24")
    add(client, lid, "whenever")
    page = client.get("/todos").text
    soon = page[page.index("Coming up"):page.index("<h2>Lists</h2>")]
    assert "Overdue one" in soon and "8 days late" in soon and "This week" in soon
    assert "Far away" not in soon and "Whenever" not in soon


def test_overview_tick_returns_the_overview(client):
    add(client, home(client), "today thing", due="2026-10-28")
    t = tasks(client)["Today thing"]
    r = client.post(f"/todos/items/{t.id}/toggle", headers={**FETCH, "X-From": "overview"})
    assert "<h2>Lists</h2>" in r.text and "Today thing" not in r.text


def test_edit_moves_task_between_lists(client):
    garden = new_list(client, "Garden", icon="🌱")
    add(client, home(client), "mow")
    t = tasks(client)["Mow"]
    assert client.get(f"/todos/items/{t.id}").status_code == 200
    r = client.post(f"/todos/items/{t.id}", data={"title": "mow the lawn", "due": "", "list_id": str(garden),
                                                 "assignee_id": "", "note": "front and back"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/todos/{garden}"
    t = tasks(client)["Mow the lawn"]
    assert (t.list_id, t.note) == (garden, "front and back")


def test_delete_task_and_list(client):
    lid = new_list(client, "Temp")
    add(client, lid, "a")
    client.post(f"/todos/items/{tasks(client)['A'].id}/delete")
    assert tasks(client) == {}
    add(client, lid, "b")
    client.post(f"/todos/{lid}/delete")
    assert "Temp" not in lists(client) and tasks(client) == {}


def test_list_needs_a_name(client):
    assert client.post("/todos", data={"name": " ", "visibility": "shared"}).status_code == 422


def test_todo_changes_bump_the_revision(client):
    before = client.get("/rev").json()["rev"]
    add(client, home(client), "x")
    assert client.get("/rev").json()["rev"] != before
