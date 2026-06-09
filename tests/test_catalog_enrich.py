"""Pure unit tests for app.catalog_enrich — NO network, NO database.

The OpenRouter HTTP call is never exercised here: ``suggest_for_article``
accepts an injectable ``caller``, and every other tested function is a pure
string/SQL builder or parser.
"""

from __future__ import annotations

from app import catalog_enrich as ce


# --------------------------------------------------------------------------- #
# Constants / config
# --------------------------------------------------------------------------- #

def test_clinical_fields_exact():
    assert ce.CLINICAL_FIELDS == frozenset({"generic_name", "composition"})
    # clinical fields must also be enrichable
    assert set(ce.CLINICAL_FIELDS).issubset(set(ce.ENRICHABLE_FIELDS))


def test_brand_name_never_enrichable():
    assert "brand_name" not in ce.ENRICHABLE_FIELDS


# --------------------------------------------------------------------------- #
# Gap-field SQL builder
# --------------------------------------------------------------------------- #

def test_gap_predicate_builds_for_each_field():
    for f in ce.ENRICHABLE_FIELDS:
        pred = ce._gap_predicate(f)
        assert f in pred
        assert "IS NULL" in pred
        assert "btrim" in pred


def test_gap_predicate_rejects_unknown_field():
    import pytest

    with pytest.raises(ValueError):
        ce._gap_predicate("brand_name")
    with pytest.raises(ValueError):
        ce._gap_predicate("id; DROP TABLE x")


def test_is_blank():
    assert ce._is_blank(None)
    assert ce._is_blank("")
    assert ce._is_blank("   ")
    assert not ce._is_blank("x")
    assert not ce._is_blank(0)  # numeric zero is a real value


# --------------------------------------------------------------------------- #
# Prompt builder
# --------------------------------------------------------------------------- #

def test_prompt_includes_brand_and_examples():
    examples = [
        {"brand_name": "Panadol", "generic_name": "Paracetamol", "category": "Analgesic"},
        {"brand_name": "Panadeine", "generic_name": "Paracetamol/Codeine"},
    ]
    prompt = ce.build_prompt(
        "Panadol Extra",
        {"brand_name": "Panadol Extra", "category": "Analgesic"},
        ["generic_name", "composition"],
        examples,
    )
    assert "Panadol Extra" in prompt
    assert "Paracetamol" in prompt          # grounded example value present
    assert "generic_name" in prompt
    assert "composition" in prompt
    assert "unknown" in prompt              # the non-answer contract
    assert "STRICT JSON" in prompt


def test_prompt_handles_no_examples():
    prompt = ce.build_prompt("Zyx", {"brand_name": "Zyx"}, ["dosage"], [])
    assert "Zyx" in prompt
    assert "no close examples" in prompt
    assert "dosage" in prompt


def test_prompt_ignores_non_enrichable_want_fields():
    prompt = ce.build_prompt("Aspro", {"brand_name": "Aspro"}, ["brand_name", "dosage"], [])
    # brand_name silently dropped, dosage kept
    assert "FILL THESE MISSING FIELDS: dosage" in prompt


# --------------------------------------------------------------------------- #
# JSON parser
# --------------------------------------------------------------------------- #

def test_parse_plain_json():
    out = ce.parse_llm_json(
        '{"generic_name": {"suggested": "Ibuprofen", "confidence": 0.9, "reason": "brand match"}}'
    )
    assert out["generic_name"]["suggested"] == "Ibuprofen"
    assert out["generic_name"]["confidence"] == 0.9
    assert out["generic_name"]["reason"] == "brand match"


def test_parse_fenced_json():
    raw = '```json\n{"category": {"suggested": "Antibiotic", "confidence": 0.7}}\n```'
    out = ce.parse_llm_json(raw)
    assert out["category"]["suggested"] == "Antibiotic"
    assert out["category"]["confidence"] == 0.7
    assert out["category"]["reason"] == ""  # missing reason → empty string


def test_parse_json_with_surrounding_prose():
    raw = 'Sure! Here is the data:\n{"dosage": {"suggested": "500mg", "confidence": 0.6}} hope that helps'
    out = ce.parse_llm_json(raw)
    assert out["dosage"]["suggested"] == "500mg"


def test_parse_drops_unknown_and_blank():
    raw = (
        '{"generic_name": {"suggested": "unknown", "confidence": 0.2},'
        ' "composition": {"suggested": "", "confidence": 0.1},'
        ' "category": {"suggested": "Vitamin", "confidence": 0.8}}'
    )
    out = ce.parse_llm_json(raw)
    assert "generic_name" not in out      # "unknown" skipped
    assert "composition" not in out       # blank skipped
    assert out["category"]["suggested"] == "Vitamin"


def test_parse_garbage_returns_empty():
    assert ce.parse_llm_json("not json at all") == {}
    assert ce.parse_llm_json("") == {}
    assert ce.parse_llm_json("```json\n{broken json,,,\n```") == {}
    assert ce.parse_llm_json("[1, 2, 3]") == {}  # list, not object


def test_parse_ignores_non_enrichable_keys_and_clamps_confidence():
    raw = (
        '{"brand_name": {"suggested": "X", "confidence": 0.9},'
        ' "category": {"suggested": "Tonic", "confidence": 5.0}}'
    )
    out = ce.parse_llm_json(raw)
    assert "brand_name" not in out
    assert out["category"]["confidence"] == 1.0  # clamped to [0,1]


def test_parse_bad_confidence_defaults_zero():
    raw = '{"dosage": {"suggested": "10ml", "confidence": "high"}}'
    out = ce.parse_llm_json(raw)
    assert out["dosage"]["confidence"] == 0.0


# --------------------------------------------------------------------------- #
# suggest_for_article with injected caller (no network)
# --------------------------------------------------------------------------- #

def test_suggest_for_article_uses_injected_caller():
    captured = {}

    def fake_caller(prompt, model, api_key):
        captured["prompt"] = prompt
        captured["model"] = model
        return '{"generic_name": {"suggested": "Amoxicillin", "confidence": 0.85, "reason": "x"}}'

    row = {"brand_name": "Amoxil", "category": "Antibiotic"}
    out = ce.suggest_for_article(
        "A123", row, ["generic_name"], [],
        caller=fake_caller, model="test-model", api_key="k",
    )
    assert out["generic_name"]["suggested"] == "Amoxicillin"
    assert captured["model"] == "test-model"
    assert "Amoxil" in captured["prompt"]


def test_suggest_for_article_failsoft_on_caller_error():
    def boom(prompt, model, api_key):
        raise RuntimeError("network down")

    out = ce.suggest_for_article(
        "A1", {"brand_name": "Z"}, ["dosage"], [],
        caller=boom, model="m", api_key="k",
    )
    assert out == {}


def test_suggest_for_article_no_wanted_fields():
    out = ce.suggest_for_article(
        "A1", {"brand_name": "Z"}, ["brand_name"], [],
        caller=lambda *a: "{}", model="m", api_key="k",
    )
    assert out == {}
