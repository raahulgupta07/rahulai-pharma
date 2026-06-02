"""Phase 8A — industry visibility policy template tests."""
from __future__ import annotations

import pytest

from dash.policy.schema import VisibilityPolicy
from dash.policy.templates import ALL, get_template, list_templates


def test_list_templates_returns_five():
    items = list_templates()
    assert len(items) == 5
    names = {i["name"] for i in items}
    assert names == {"pharmacy", "retail", "hotel", "bank", "generic"}
    for it in items:
        assert it["label"]
        assert it["description"]
        assert it["scope_keyword"]
        assert it["icon"]
        assert isinstance(it["field_count"], int)
        assert it["field_count"] >= 0


@pytest.mark.parametrize("name", list(ALL.keys()))
def test_template_validates_via_pydantic(name):
    tmpl = get_template(name)
    assert tmpl is not None
    pol = VisibilityPolicy(**tmpl["policy"])
    assert pol.version == 1
    assert pol.private is not None
    assert pol.network is not None
    assert pol.public is not None


def test_pharmacy_includes_patient_id():
    tmpl = get_template("pharmacy")
    assert tmpl is not None
    network_fields = tmpl["policy"]["network"]["fields"]
    public_fields = tmpl["policy"]["public"]["fields"]
    assert "patient_id" in network_fields
    assert "patient_id" in public_fields
    assert network_fields["patient_id"]["mode"] == "hide"


def test_get_template_unknown_returns_none():
    assert get_template("nonexistent") is None


def test_apply_template_merge_preserves_existing_fields():
    """Merge mode: template fields override; existing fields not in template kept.

    Replicates the merge logic in app.learning.visibility_policy_apply_template
    without importing the FastAPI app (which requires pgvector at import time).
    """
    existing_doc = {
        "version": 3,
        "private": {"fields": {}},
        "network": {"fields": {
            "custom_field": {"mode": "hide"},
            "qty": {"mode": "full"},
        }},
        "public": {"fields": {
            "legacy_field": {"mode": "mask", "mask_with": "L-***"},
        }},
    }
    tmpl = get_template("pharmacy")
    tmpl_policy = tmpl["policy"]

    merged_doc = {"version": existing_doc.get("version", 1)}
    for aud in ("private", "network", "public"):
        existing_fields = dict((existing_doc.get(aud) or {}).get("fields") or {})
        tmpl_fields = dict((tmpl_policy.get(aud) or {}).get("fields") or {})
        existing_fields.update(tmpl_fields)
        merged_doc[aud] = {"fields": existing_fields}

    pol = VisibilityPolicy(**merged_doc)
    # Merge: existing custom_field preserved
    assert "custom_field" in pol.network.fields
    # Merge: template's patient_id added
    assert "patient_id" in pol.network.fields
    # Merge: template's qty overrides existing qty (now band, not full)
    assert pol.network.fields["qty"].mode == "band"
    # Merge: legacy_field in public preserved
    assert "legacy_field" in pol.public.fields
    assert pol.public.fields["legacy_field"].mode == "mask"
