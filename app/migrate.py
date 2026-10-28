"""Apply database migrations. Runs on app startup (single replica)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from .db import Database


def upgrade(db: Database) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with db.engine.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            # SQLite migrations rebuild tables (copy, drop, rename). With foreign keys
            # on, dropping the old table fires ON DELETE CASCADE / SET NULL on every
            # row that points at it, e.g. clearing every item's category when the
            # category table is rebuilt. The pragma only takes effect outside a transaction.
            connection.connection.driver_connection.execute("PRAGMA foreign_keys=OFF")
        try:
            with connection.begin():
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "head")
                if sqlite:
                    broken = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
                    if broken:
                        raise RuntimeError(f"migration left broken foreign keys: {broken[:5]}")
        finally:
            if sqlite:
                connection.connection.driver_connection.execute("PRAGMA foreign_keys=ON")
