import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_every_static_file_and_template_is_packaged():
    """The image installs the app with pip, so a file the package-data globs miss isn't served
    (Training Planner once lost its vendored scripts that way)."""
    globs = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["app"]
    app = ROOT / "app"
    packaged = {p for g in globs for p in app.glob(g) if p.is_file()}
    wanted = {p for d in ("static", "templates") for p in (app / d).rglob("*") if p.is_file()}
    assert sorted(str(p.relative_to(app)) for p in wanted - packaged) == []
