import sqlite3

from app.backup import backup


def test_backup_copies_and_verifies(tmp_path):
    db = tmp_path / "home.db"
    app_conn = sqlite3.connect(db)  # the running app keeps a WAL connection open
    app_conn.execute("PRAGMA journal_mode=WAL")
    with app_conn:
        app_conn.execute("create table t (x)")
        app_conn.execute("insert into t values (42)")
    out = backup(db, tmp_path / "backups", keep_days=30)
    app_conn.close()
    assert sqlite3.connect(out).execute("select x from t").fetchone() == (42,)


def test_prefix_names_the_file(tmp_path):
    db = tmp_path / "home.db"
    sqlite3.connect(db).close()
    out = backup(db, tmp_path / "backups", keep_days=30, prefix="home-predeploy")
    assert out.name.startswith("home-predeploy-")


def test_skip_if_missing(tmp_path, capsys):
    from app.backup import main

    assert main([str(tmp_path / "missing.db"), str(tmp_path / "b"), "--skip-if-missing"]) == 0
    assert "nothing to back up" in capsys.readouterr().out
    assert not (tmp_path / "b").exists()
