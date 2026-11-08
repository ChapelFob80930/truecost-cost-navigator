"""The README's architecture section must stay in step with the code.

If this fails, you changed the architecture: update README.md (diagram, components table, API table) in the same change.
"""
import dataclasses
import re
from pathlib import Path

from app.container import Container
from app.main import create_app

from .conftest import FakeRepo

def route_paths(app):
    """Every route path, in old and new FastAPI. Newer versions keep included routers inside an `_IncludedRouter` object
    (reachable through `original_router`) instead of listing their routes in `app.routes`."""
    out = set()

    def walk(routes):
        for r in routes:
            nested = getattr(getattr(r, "original_router", None), "routes", None)
            if nested is not None:
                walk(nested)
            elif getattr(r, "methods", None):
                out.add(r.path)
    walk(app.routes)
    return out


README = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
SKIP = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def test_every_route_is_documented_in_the_readme():
    app = create_app(container=Container.from_repo(FakeRepo()))
    paths = route_paths(app) - SKIP
    missing = sorted(p for p in paths if f"`{p}`" not in README)
    assert not missing, f"Add these routes to the API table in README.md: {missing}"


def test_every_container_component_is_in_the_components_table():
    names = [f.name for f in dataclasses.fields(Container)]
    missing = [n for n in names if f"| `{n}` |" not in README]
    assert not missing, f"Add these components to the Components table in README.md: {missing}"


def test_readme_has_the_architecture_diagram():
    blocks = re.findall(r"```mermaid\n(.*?)```", README, flags=re.S)
    assert len(blocks) >= 3, "README.md should keep its three Mermaid diagrams (architecture, price, main flow)"
    assert any("flowchart" in b and "Tiger" in b and "Gemini" in b for b in blocks), "architecture diagram must show Tiger Data and Gemini"


def test_documented_routes_still_exist():
    """The reverse check: no stale rows in the API table."""
    app = create_app(container=Container.from_repo(FakeRepo()))
    paths = route_paths(app)
    table = README.split("## API", 1)[1].split("## Guardrails", 1)[0]
    documented = set(re.findall(r"^\| `(/[^`]*)` \|", table, flags=re.M))
    stale = sorted(documented - paths)
    assert not stale, f"README.md lists routes that no longer exist: {stale}"
