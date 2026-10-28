from sqlalchemy import select

from app import watch
from app.models import Show, User
from tests.conftest import FETCH


def add(client, title, kind="series", headers=None):
    return client.post("/watch/shows", data={"title": title, "kind": kind}, headers=headers, follow_redirects=False)


def pick(client, kind="", headers=None):
    return client.post("/watch/pick", data={"kind": kind}, headers=headers, follow_redirects=False)


def shows(client):
    with client.app.state.db.sessionmaker() as s:
        return {x.title: x for x in s.scalars(select(Show))}


class First:
    """A stand-in for random: always takes the first choice offered."""

    def choice(self, seq):
        return seq[0]


class Last:
    def choice(self, seq):
        return seq[-1]


def test_add_per_person(client, maguy):
    assert add(client, "  The   Bear ").headers["location"] == "/watch"
    add(maguy, "Dune", kind="movie")
    page = client.get("/watch").text
    assert "Pedro's ideas" in page and "Maguy's ideas" in page and "The Bear" in page and "Dune" in page
    assert shows(client)["Dune"].kind == "movie" and shows(client)["The Bear"].added_by.username == "pedro"


def test_add_rejects_empty_and_duplicates(client, maguy):
    assert add(client, "   ").status_code == 422
    add(client, "Severance")
    r = add(maguy, "severance", headers=FETCH)
    assert r.status_code == 422 and "already on the list" in r.text
    assert len(shows(client)) == 1


def test_nothing_to_pick(client):
    assert "Nothing to pick from yet" in client.get("/watch").text
    r = pick(client, headers=FETCH)
    assert r.status_code == 422 and "No shows to pick from" in r.text


def test_pick_watch_and_history(client, maguy):
    add(client, "The Bear")
    r = pick(maguy, headers=FETCH)
    assert r.status_code == 200 and "Up next" in r.text and "picked by Maguy" in r.text and "<html" not in r.text
    bear = shows(client)["The Bear"]
    assert bear.picked_at is not None and bear.picked_by.username == "maguy"
    assert client.post(f"/watch/shows/{bear.id}/watched").status_code == 200
    bear = shows(client)["The Bear"]
    assert bear.watched_at is not None
    page = client.get("/watch").text
    assert "Up next" not in page and "Watched" in page
    # Watched shows can go on the list again.
    assert add(client, "The Bear").status_code == 303


def test_pick_another_changes_the_pick(client, maguy):
    add(client, "The Bear")
    add(maguy, "Slow Horses")
    pick(client)
    first = next(s for s in shows(client).values() if s.picked_at)
    pick(client, kind="series")
    now = [s for s in shows(client).values() if s.picked_at]
    assert len(now) == 1 and now[0].title != first.title
    # The only other show is gone: nothing else to pick, and the pick stays.
    client.post(f"/watch/shows/{first.id}/delete")
    r = pick(client, kind="series", headers=FETCH)
    assert r.status_code == 422 and "No other series" in r.text
    assert [s.title for s in shows(client).values() if s.picked_at] == [now[0].title]


def test_pick_by_kind(client):
    add(client, "The Bear")
    add(client, "Dune", kind="movie")
    for _ in range(5):
        pick(client, kind="movie")
        assert [s.title for s in shows(client).values() if s.picked_at] == ["Dune"]
        client.post(f"/watch/shows/{shows(client)['Dune'].id}/put-back")
    assert not any(s.picked_at for s in shows(client).values())


def test_each_person_gets_the_same_chance(client, maguy):
    """Pedro adding ten shows doesn't make his ten times likelier: a person is drawn first, then a show."""
    for i in range(10):
        add(client, f"Pedro {i}")
    add(maguy, "Maguy's one")
    with client.app.state.db.sessionmaker() as s:
        maguy_user = s.scalar(select(User).where(User.username == "maguy"))
        assert watch.pick(s, maguy_user, rng=Last()).title == "Maguy's one"
        s.rollback()
        assert watch.pick(s, maguy_user, rng=First()).title == "Pedro 0"


def test_missing_show_is_404(client):
    for path in ("/watch/shows/9/delete", "/watch/shows/9/watched", "/watch/shows/9/put-back"):
        assert client.post(path).status_code == 404, path


def test_fragment(client):
    add(client, "Dune", kind="movie")
    r = client.get("/watch/fragment")
    assert "Dune" in r.text and "<html" not in r.text
