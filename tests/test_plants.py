from datetime import date, timedelta

from sqlalchemy import select

from app import plants
from app.models import Photo, Plant, Watering
from tests.conftest import FETCH, TODAY

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50


def new(client, name="Monstera", every="7", spot="Living room", notes=""):
    r = client.post("/plants", data={"name": name, "every_days": every, "spot": spot, "notes": notes}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def water(client, pid, day=None, back="", headers=None):
    data = {"back": back} | ({"day": day.isoformat()} if day else {})
    return client.post(f"/plants/{pid}/water", data=data, headers=headers, follow_redirects=False)


def db(client):
    return client.app.state.db.sessionmaker()


def logged(client, pid):
    with db(client) as s:
        return sorted((w.day, w.by.username) for w in s.scalars(select(Watering).where(Watering.plant_id == pid)))


def test_add_and_edit(client):
    assert "No plants yet" in client.get("/plants").text
    pid = new(client, name="  Monstera   deliciosa ", every="")
    with db(client) as s:
        p = s.get(Plant, pid)
        assert p.name == "Monstera deliciosa" and p.every_days is None and p.spot == "Living room"
    r = client.post(f"/plants/{pid}/edit", data={"name": "Monstera", "every_days": "10", "spot": ""}, follow_redirects=False)
    assert r.headers["location"] == f"/plants/{pid}"
    page = client.get(f"/plants/{pid}").text
    assert "Every 10 days" in page and "Not watered yet" in page


def test_form_errors(client):
    for data, msg in [({"name": " "}, "Give the plant a name"), ({"name": "Fern", "every_days": "often"}, "number of days"),
                      ({"name": "Fern", "every_days": "0"}, "between 1 and 365")]:
        r = client.post("/plants", data=data)
        assert r.status_code == 422 and msg in r.text
    with db(client) as s:
        assert not s.scalars(select(Plant)).all()


def test_status_and_order(client, maguy):
    fern, cactus, ivy, basil, pothos = (new(client, n, e) for n, e in
                                         [("Fern", "3"), ("Cactus", "14"), ("Ivy", ""), ("Basil", "2"), ("Pothos", "7")])
    water(client, fern, TODAY - timedelta(days=5))     # 2 days late
    water(maguy, cactus, TODAY - timedelta(days=13))   # tomorrow
    water(client, ivy, TODAY - timedelta(days=20))     # no schedule
    water(maguy, basil, TODAY - timedelta(days=2))     # today
    water(client, pothos)                              # watered today
    with db(client) as s:
        rows = plants.overview(s, TODAY)
        assert [(r.plant.name, r.text, r.kind) for r in rows] == [
            ("Fern", "2 days late", "late"), ("Basil", "Water today", "today"), ("Cactus", "Tomorrow", "soon"),
            ("Pothos", "Watered today", "done"), ("Ivy", "", "")]
        assert plants.due_count(s, TODAY) == 2
    page = client.get("/plants").text
    assert page.index("Fern") < page.index("Basil") < page.index("Cactus") < page.index("Pothos") < page.index("Ivy")
    assert "Watered on 8 Oct by Pedro" in page
    assert "Watered yesterday" not in page and "Watered today by Pedro" in page
    # The menu shows how many need water, on every page.
    assert '<span class="count" title="2 need water">2</span>' in client.get("/shopping").text
    assert 'class="dot"' in client.get("/recipes").text


def test_never_watered_with_a_schedule_needs_water(client):
    new(client, "Fern", "3")
    new(client, "Ivy", "")
    with db(client) as s:
        assert plants.due_count(s, TODAY) == 1
        assert [r.text for r in plants.overview(s, TODAY)] == ["Needs water", ""]


def test_water_from_the_list_once_a_day_and_undo(client, maguy):
    pid = new(client)
    r = water(maguy, pid, back="list", headers=FETCH)
    assert r.status_code == 200 and "<html" not in r.text and "Watered today by Maguy" in r.text and "Undo" in r.text
    water(client, pid, back="list")  # Pedro taps it too: still one watering, Maguy's
    assert logged(client, pid) == [(TODAY, "maguy")]
    assert water(client, pid, back="list").headers["location"] == "/plants"
    r = client.post(f"/plants/{pid}/undo", data={"back": "list"}, headers=FETCH)
    assert r.status_code == 200 and "Watered</span>" in r.text
    assert logged(client, pid) == []


def test_log_an_earlier_day_and_remove_it(client):
    pid = new(client)
    yesterday = TODAY - timedelta(days=1)
    assert water(client, pid, yesterday).headers["location"] == f"/plants/{pid}"
    r = water(client, pid, TODAY + timedelta(days=1))
    assert r.status_code == 422 and "happened yet" in r.text
    assert "Watered yesterday by Pedro" in client.get(f"/plants/{pid}").text
    with db(client) as s:
        wid = s.scalar(select(Watering.id))
    client.post(f"/plants/{pid}/waterings/{wid}/delete")
    assert logged(client, pid) == []


def test_average_gap(client):
    pid = new(client)
    for d in (10, 6, 2):
        water(client, pid, TODAY - timedelta(days=d))
    assert "on average every 4 days so far" in client.get(f"/plants/{pid}").text


def test_photos(client):
    pid = new(client)
    r = client.post(f"/plants/{pid}/photos", files={"photo": ("p.jpg", JPEG, "image/jpeg"), "thumb": ("t.jpg", PNG, "image/jpeg")},
                    follow_redirects=False)
    assert r.headers["location"] == f"/plants/{pid}"
    with db(client) as s:
        ph = s.scalar(select(Photo))
        assert ph.plant_id == pid and ph.content_type == "image/jpeg"
    full = client.get(f"/photos/{ph.id}")
    assert full.content == JPEG and full.headers["content-type"] == "image/jpeg" and "immutable" in full.headers["cache-control"]
    thumb = client.get(f"/photos/{ph.id}/thumb")
    assert thumb.content == PNG and thumb.headers["content-type"] == "image/png"
    assert f"/photos/{ph.id}/thumb" in client.get("/plants").text
    client.post(f"/photos/{ph.id}/delete")
    assert client.get(f"/photos/{ph.id}").status_code == 404


def test_photo_without_thumbnail_and_bad_files(client):
    pid = new(client)
    client.post(f"/plants/{pid}/photos", files={"photo": ("p.png", PNG, "image/png")})
    with db(client) as s:
        ph = s.scalar(select(Photo))
    assert client.get(f"/photos/{ph.id}/thumb").content == PNG
    for data, msg in [(b"GIF89a....", "a photo the app can show"), (b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024), "too big")]:
        r = client.post(f"/plants/{pid}/photos", files={"photo": ("x", data, "image/jpeg")})
        assert r.status_code == 422 and msg in r.text
    r = client.post(f"/plants/{pid}/photos", data={"x": "1"})
    assert r.status_code == 422 and "Choose a photo" in r.text


def test_delete_plant_takes_its_history_and_photos(client):
    pid = new(client)
    water(client, pid)
    client.post(f"/plants/{pid}/photos", files={"photo": ("p.jpg", JPEG, "image/jpeg")})
    assert client.post(f"/plants/{pid}/delete", follow_redirects=False).headers["location"] == "/plants"
    with db(client) as s:
        assert not s.scalars(select(Watering)).all() and not s.scalars(select(Photo)).all()
    assert client.get(f"/plants/{pid}").status_code == 404


def test_ago():
    assert [plants.ago(TODAY - timedelta(days=n), TODAY) for n in (0, 1, 3, 9)] == [
        "today", "yesterday", "3 days ago", "on 19 Oct"]
    assert plants.ago(date(2025, 12, 30), TODAY) == "on 30 Dec 2025"
