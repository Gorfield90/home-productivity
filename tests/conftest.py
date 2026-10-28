from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

USERS = [("pedro", "Pedro"), ("maguy", "Maguy")]
TODAY = date(2026, 10, 28)  # a Wednesday


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(database_url=f"sqlite:///{tmp_path}/test.db", users=USERS), today=lambda: TODAY)


@pytest.fixture
def client(app):
    with TestClient(app, headers={"X-authentik-username": "pedro"}) as c:
        yield c


@pytest.fixture
def maguy(app, client):
    """A second browser, logged in as Maguy, on the same database."""
    with TestClient(app, headers={"X-authentik-username": "maguy"}) as c:
        yield c


FETCH = {"X-Requested-With": "fetch"}


def add(client, text, headers=None, **fields):
    return client.post("/shopping/items", data={"text": text, **fields}, headers=headers, follow_redirects=False)


def items(client):
    """What is on the list right now, as {name: item}."""
    from app.models import Item
    from sqlalchemy import select

    with client.app.state.db.sessionmaker() as s:
        return {i.name: i for i in s.scalars(select(Item).where(Item.bought_at.is_(None)))}
