"""Pure unit tests for app.catalog_enrich auto-apply — NO network, NO database.

Only the DB-free safety logic is exercised here: ``_resolve_autoapply_fields``
(a pure function) and the module constants. ``auto_apply_low_risk`` itself runs
a single UPDATE against Postgres, so it is intentionally NOT called here — its
entire guard surface lives in the pure resolver, which IS tested.
"""

from __future__ import annotations

from app import catalog_enrich as ce


# --------------------------------------------------------------------------- #
# Constants / invariants
# --------------------------------------------------------------------------- #

def test_low_risk_fields_exact():
    assert ce.LOW_RISK_FIELDS == frozenset({"category", "indication"})
    # low-risk fields must be real enrichable fields
    assert set(ce.LOW_RISK_FIELDS).issubset(set(ce.ENRICHABLE_FIELDS))


def test_low_risk_disjoint_from_clinical():
    # The core safety invariant: a clinical field can never also be low-risk.
    assert ce.LOW_RISK_FIELDS & ce.CLINICAL_FIELDS == frozenset()


def test_autoapply_min_conf_sane():
    assert isinstance(ce.AUTOAPPLY_MIN_CONF, float)
    assert 0.0 < ce.AUTOAPPLY_MIN_CONF <= 1.0


# --------------------------------------------------------------------------- #
# _resolve_autoapply_fields — pure guard logic
# --------------------------------------------------------------------------- #

def test_resolve_none_returns_all_low_risk():
    assert set(ce._resolve_autoapply_fields(None)) == {"category", "indication"}


def test_resolve_default_arg_returns_all_low_risk():
    # calling with no argument behaves like None
    assert set(ce._resolve_autoapply_fields()) == {"category", "indication"}


def test_resolve_keeps_low_risk_field():
    assert ce._resolve_autoapply_fields(["category"]) == ["category"]


def test_resolve_drops_clinical_fields():
    # generic_name / composition are NEVER auto-applied, even if requested
    assert ce._resolve_autoapply_fields(["generic_name"]) == []
    assert ce._resolve_autoapply_fields(["composition"]) == []
    assert ce._resolve_autoapply_fields(["generic_name", "composition"]) == []


def test_resolve_drops_med_risk_fields():
    # dosage / side_effect are med-risk — pending only, never auto-applied
    assert ce._resolve_autoapply_fields(["dosage"]) == []
    assert ce._resolve_autoapply_fields(["side_effect"]) == []


def test_resolve_drops_unknown_and_junk_fields():
    assert ce._resolve_autoapply_fields(["brand_name"]) == []
    assert ce._resolve_autoapply_fields(["id; DROP TABLE x"]) == []
    assert ce._resolve_autoapply_fields([""]) == []


def test_resolve_mixed_request_keeps_only_low_risk():
    got = ce._resolve_autoapply_fields(
        ["category", "generic_name", "dosage", "indication", "side_effect"]
    )
    assert set(got) == {"category", "indication"}


def test_resolve_is_deterministic_sorted_and_deduped():
    got = ce._resolve_autoapply_fields(["indication", "category", "category"])
    assert got == ["category", "indication"]  # sorted + unique
