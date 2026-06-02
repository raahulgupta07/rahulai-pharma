"""Phase 10A skill drafter tests."""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── In-memory fake DB engine ──────────────────────────────────────────────
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def mappings(self):
        class _M:
            def __init__(self, rows):
                self._rows = rows

            def first(self):
                return self._rows[0] if self._rows else None

            def all(self):
                return list(self._rows)

        return _M(self._rows)

    def all(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        sql = str(stmt).strip().upper()
        params = params or {}
        rows = self.store["rows"]

        if sql.startswith("INSERT INTO DASH.DASH_SKILL_DRAFTS"):
            row = {
                "id": params.get("id"),
                "project_slug": params.get("ps"),
                "proposed_name": params.get("nm"),
                "proposed_description": params.get("ds"),
                "proposed_skill_md": params.get("md"),
                "frontmatter": params.get("fm"),
                "status": params.get("st", "pending"),
                "promoted_skill_id": None,
            }
            rows.append(row)
            return _FakeResult([])

        if sql.startswith("SELECT ID FROM DASH.DASH_SKILL_DRAFTS"):
            ps = params.get("ps")
            nm = params.get("nm")
            for r in rows:
                same_ps = (
                    (ps is None and r["project_slug"] is None) or r["project_slug"] == ps
                )
                if same_ps and r["proposed_name"] == nm and r["status"] == "pending":
                    return _FakeResult([(r["id"],)])
            return _FakeResult([])

        if sql.startswith("SELECT STATUS FROM DASH.DASH_SKILL_DRAFTS"):
            rid = params.get("id")
            for r in rows:
                if r["id"] == rid:
                    return _FakeResult([(r["status"],)])
            return _FakeResult([])

        if sql.startswith("SELECT ID, PROJECT_SLUG, PROPOSED_NAME"):
            rid = params.get("id")
            for r in rows:
                if r["id"] == rid:
                    return _FakeResult([r])
            return _FakeResult([])

        if sql.startswith("UPDATE DASH.DASH_SKILL_DRAFTS"):
            rid = params.get("id")
            for r in rows:
                if r["id"] == rid:
                    if "STATUS = 'REJECTED'" in sql:
                        r["status"] = "rejected"
                    elif "STATUS = 'APPROVED'" in sql:
                        r["status"] = "approved"
                        r["promoted_skill_id"] = params.get("sid")
                    break
            return _FakeResult([])

        return _FakeResult([])


class _FakeEngine:
    def __init__(self):
        self.store = {"rows": []}

    def connect(self):
        return _FakeConn(self.store)

    def begin(self):
        return _FakeConn(self.store)


# ── Tests ──────────────────────────────────────────────────────────────────
def test_migration_file_exists():
    p = ROOT / "db" / "migrations" / "052_skill_drafts.sql"
    assert p.exists(), f"missing migration: {p}"
    text = p.read_text()
    assert "dash.dash_skill_drafts" in text
    assert "idx_sd_status" in text
    assert "idx_sd_project" in text


def test_flag_off_returns_disabled_no_insert(monkeypatch):
    from dash.skills import drafter
    monkeypatch.delenv("EXPERIMENTAL_AGI", raising=False)
    fake = _FakeEngine()
    monkeypatch.setattr(drafter, "_get_engine", lambda: fake)
    result = drafter.draft_skill(
        conversation_excerpt="Q: how to compute RFM? A: ...",
        trigger_phrase="save as skill",
    )
    assert result["ok"] is False
    assert result["reason"] == "disabled"
    assert fake.store["rows"] == []


def test_flag_on_valid_llm_creates_draft(monkeypatch):
    from dash.skills import drafter
    monkeypatch.setenv("EXPERIMENTAL_AGI", "1")
    fake = _FakeEngine()
    monkeypatch.setattr(drafter, "_get_engine", lambda: fake)

    payload = {
        "name": "RFM Segmentation",
        "description": "Use when user asks to segment customers by recency, frequency, monetary.",
        "trigger_keywords": ["rfm", "segment customers", "recency frequency"],
        "allowed_tools": ["rfm_score", "discover_tables"],
        "body_markdown": "## Overview\nSegment customers.\n## Instructions\n1. Call rfm_score.\n## Examples\nQ: ...\n## Edge Cases\nNo transactions table.",
    }
    monkeypatch.setattr(drafter, "_call_llm", lambda prompt, task="deep_analysis": json.dumps(payload))

    result = drafter.draft_skill(
        conversation_excerpt="Q: segment customers? A: used rfm_score tool ...",
        trigger_phrase="save this as a skill",
        project_slug="proj_demo",
    )
    assert result["ok"] is True, result
    assert result["draft_id"].startswith("sd_")
    assert result["proposed_name"] == "rfm-segmentation"
    assert "trigger_keywords" in result["frontmatter"]
    assert result["frontmatter"]["description"].endswith(".")
    # SKILL.md round-trip via parse_frontmatter
    fm, body = drafter.parse_frontmatter(result["skill_md"])
    assert fm["name"] == "rfm-segmentation"
    assert isinstance(fm.get("trigger_keywords"), list)
    assert "Overview" in body or "rfm" in body.lower()
    assert len(fake.store["rows"]) == 1


def test_parse_frontmatter_strict_and_malformed():
    from dash.skills.drafter import parse_frontmatter

    md = (
        "---\n"
        "name: my-skill\n"
        "description: Does a thing\n"
        'trigger_keywords: ["a", "b", "c"]\n'
        "allowed_tools: []\n"
        "---\n"
        "# Body\nHello"
    )
    fm, body = parse_frontmatter(md)
    assert fm["name"] == "my-skill"
    assert fm["description"] == "Does a thing"
    assert fm["trigger_keywords"] == ["a", "b", "c"]
    assert fm["allowed_tools"] == []
    assert "Hello" in body

    # Malformed: no frontmatter boundaries
    fm2, body2 = parse_frontmatter("Just a regular doc with no fm")
    assert fm2 == {}
    assert body2 == "Just a regular doc with no fm"

    # Malformed: empty input
    fm3, body3 = parse_frontmatter("")
    assert fm3 == {}

    # Inline list w/o quotes
    md4 = "---\nname: x\ntrigger_keywords: [a, b]\n---\nbody"
    fm4, _ = parse_frontmatter(md4)
    assert fm4["trigger_keywords"] == ["a", "b"]


def test_reject_draft_idempotent(monkeypatch):
    from dash.skills import drafter
    fake = _FakeEngine()
    monkeypatch.setattr(drafter, "_get_engine", lambda: fake)
    # Seed a pending draft directly
    fake.store["rows"].append({
        "id": "sd_abc12345",
        "project_slug": None,
        "proposed_name": "x",
        "proposed_description": "d",
        "proposed_skill_md": "---\nname: x\n---\nbody",
        "frontmatter": json.dumps({"name": "x"}),
        "status": "pending",
        "promoted_skill_id": None,
    })

    r1 = drafter.reject_draft("sd_abc12345", "not useful", approver_id=1)
    assert r1["ok"] is True
    assert r1.get("status") == "rejected"

    r2 = drafter.reject_draft("sd_abc12345", "still not useful", approver_id=1)
    assert r2["ok"] is True
    assert r2.get("already_rejected") is True

    # Not found
    r3 = drafter.reject_draft("sd_nope", "x")
    assert r3["ok"] is False
    assert r3["reason"] == "not_found"


def test_approve_draft_promotes_to_registry(monkeypatch):
    from dash.skills import drafter
    fake = _FakeEngine()
    monkeypatch.setattr(drafter, "_get_engine", lambda: fake)

    fake.store["rows"].append({
        "id": "sd_promoteme",
        "project_slug": "proj_demo",
        "proposed_name": "rfm-segmentation",
        "proposed_description": "Segment customers.",
        "proposed_skill_md": (
            "---\nname: rfm-segmentation\n"
            'description: Segment customers.\n'
            'trigger_keywords: ["rfm", "segment"]\n'
            'allowed_tools: ["rfm_score"]\n'
            "---\n# RFM\n## Instructions\nDo X."
        ),
        "frontmatter": {
            "name": "rfm-segmentation",
            "description": "Segment customers.",
            "trigger_keywords": ["rfm", "segment"],
            "allowed_tools": ["rfm_score"],
        },
        "status": "pending",
        "promoted_skill_id": None,
    })

    captured = {}

    def _fake_register(meta):
        captured["meta"] = meta
        return "skl_deadbeef"

    # Patch the registry.register_skill symbol that drafter imports lazily
    import dash.skills.registry as reg
    monkeypatch.setattr(reg, "register_skill", _fake_register)

    result = drafter.approve_draft("sd_promoteme", approver_id=42)
    assert result["ok"] is True, result
    assert result["skill_id"] == "skl_deadbeef"
    assert captured["meta"]["name"] == "rfm-segmentation"
    assert captured["meta"]["project_slug"] == "proj_demo"
    assert "rfm" in (captured["meta"]["trigger_keywords"] or [])
    assert captured["meta"]["tools"] == [{"name": "rfm_score"}]
    # Instructions = body (not frontmatter)
    assert "## Instructions" in captured["meta"]["instructions"]

    # Verify DB row updated
    row = fake.store["rows"][0]
    assert row["status"] == "approved"
    assert row["promoted_skill_id"] == "skl_deadbeef"

    # Idempotent re-approve
    result2 = drafter.approve_draft("sd_promoteme", approver_id=42)
    assert result2["ok"] is True
    assert result2.get("already_approved") is True
    assert result2["skill_id"] == "skl_deadbeef"
