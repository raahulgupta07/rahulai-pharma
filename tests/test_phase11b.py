"""Phase 11B smoke tests — skill drafts + custom agents."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_skill_drafts_router_imports():
    from app.skill_drafts_api import router
    assert router is not None
    assert router.prefix == "/api/skill-drafts"


def test_custom_agents_router_imports():
    from app.custom_agents_api import router
    assert router is not None
    assert router.prefix == "/api/custom-agents"


def test_migration_052_exists():
    p = ROOT / "db" / "migrations" / "052_skill_drafts.sql"
    assert p.exists(), f"migration 052 missing at {p}"


def test_migration_053_exists():
    # Migration 053 (custom agents) is owned by a parallel agent; this test
    # documents the dependency. If absent, mark as expected pending.
    p = ROOT / "db" / "migrations" / "053_custom_agents.sql"
    if not p.exists():
        # find any 053_*.sql variant
        cands = list((ROOT / "db" / "migrations").glob("053_*.sql"))
        assert cands, "migration 053_* not yet shipped (owned by parallel agent)"


def test_drafts_route_exists():
    p = ROOT / "frontend" / "src" / "routes" / "os" / "drafts" / "+page.svelte"
    assert p.exists()
    text = p.read_text()
    assert "/api/skill-drafts" in text


def test_agents_route_exists():
    p = ROOT / "frontend" / "src" / "routes" / "os" / "agents" / "+page.svelte"
    assert p.exists()
    text = p.read_text()
    assert "/api/custom-agents" in text


def test_layout_has_custom_nav():
    p = ROOT / "frontend" / "src" / "routes" / "+layout.svelte"
    text = p.read_text()
    assert "/ui/os/agents" in text
