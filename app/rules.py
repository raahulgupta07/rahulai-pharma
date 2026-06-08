"""
Rules API
=========

CRUD for user-defined business rules per project.
Rules are stored as JSON files in knowledge/{project_slug}/rules/.
"""

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from dash.paths import KNOWLEDGE_DIR

router = APIRouter(prefix="/api/projects", tags=["Rules"])


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_access(user: dict, slug: str):
    from app.auth import check_project_permission
    perm = check_project_permission(user, slug)
    if not perm:
        raise HTTPException(403, "Access denied")


def _rules_dir(slug: str) -> Path:
    d = KNOWLEDGE_DIR / slug / "rules"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/{slug}/rules")
def list_rules(slug: str, request: Request):
    """List all rules for a project.

    Unions two sources so the UI matches the Cockpit Brain `rule` count:
      1. JSON files in knowledge/{slug}/rules/  (user-created via this endpoint)
      2. public.dash_rules_db rows              (training pipeline + NL→SQL +
         consolidator + provider XMLA + suggest_rules promotions write here)
    Dedupe by rule id; JSON-file rule wins on conflict (it's the editable copy).
    """
    user = _get_user(request)
    _check_access(user, slug)
    rules_dir = _rules_dir(slug)
    rules: list[dict] = []
    seen_ids: set[str] = set()

    # 1) JSON-file rules (user-created)
    for f in sorted(rules_dir.glob("*.json")):
        try:
            with open(f) as fh:
                data = json.load(fh)
            rid = str(data.get("id") or f.stem)
            data["id"] = rid
            seen_ids.add(rid)
            rules.append(data)
        except Exception:
            pass

    # 2) DB rules (training-pipeline / consolidator / NL→SQL / XMLA / etc.)
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        engine = get_sql_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT en.rule_id, en.name, en.type, en.category, en.definition, en.source, en.created_at, "
                "       my.definition AS definition_my "
                "FROM public.dash_rules_db en "
                "LEFT JOIN public.dash_rules_db my ON my.rule_id = en.rule_id || '_my' "
                "  AND my.project_slug = en.project_slug AND my.lang = 'my' "
                "WHERE en.project_slug = :s AND (en.lang IS NULL OR en.lang = 'en') "
                "ORDER BY en.created_at DESC"
            ), {"s": slug}).fetchall()
        for r in rows:
            rid = str(r[0])
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            rules.append({
                "id": rid,
                "name": r[1],
                "type": r[2] or "business_rule",
                "category": r[3] or "general",
                "definition": r[4],
                "source": r[5] or "training",
                "created_at": str(r[6]) if r[6] else None,
                "definition_my": r[7],
            })
    except Exception:
        # Fail soft — still return JSON-file rules even if DB read fails
        pass

    return {"rules": rules}


@router.post("/{slug}/rules")
def create_rule(slug: str, request: Request, name: str, definition: str, type: str = "business_rule", category: str = "general"):
    """Create a new business rule for a project."""
    user = _get_user(request)
    _check_access(user, slug)
    if not name or not definition:
        raise HTTPException(400, "Name and definition required")

    rule_id = f"rule_{int(time.time() * 1000)}"
    rule = {
        "id": rule_id,
        "name": name,
        "type": type,
        "category": category,
        "definition": definition,
        "source": "user",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    rules_dir = _rules_dir(slug)
    with open(rules_dir / f"{rule_id}.json", "w") as f:
        json.dump(rule, f, indent=2)

    # Re-index rules into project knowledge
    _reindex_rules(slug)

    return {"status": "ok", "rule": rule}


@router.put("/{slug}/rules/{rule_id}")
def update_rule(slug: str, rule_id: str, request: Request, name: str = "", definition: str = "", type: str = "", category: str = ""):
    """Update an existing rule."""
    user = _get_user(request)
    _check_access(user, slug)
    rules_dir = _rules_dir(slug)
    filepath = rules_dir / f"{rule_id}.json"

    if not filepath.exists():
        raise HTTPException(404, "Rule not found")

    with open(filepath) as f:
        rule = json.load(f)

    if name:
        rule["name"] = name
    if definition:
        rule["definition"] = definition
    if type:
        rule["type"] = type
    if category:
        rule["category"] = category

    with open(filepath, "w") as f:
        json.dump(rule, f, indent=2)

    _reindex_rules(slug)
    return {"status": "ok", "rule": rule}


@router.delete("/{slug}/rules/{rule_id}")
def delete_rule(slug: str, rule_id: str, request: Request):
    """Delete a rule (JSON-file or dash_rules_db row)."""
    user = _get_user(request)
    _check_access(user, slug)
    rules_dir = _rules_dir(slug)
    filepath = rules_dir / f"{rule_id}.json"

    if filepath.exists():
        filepath.unlink()
    else:
        # Try DB delete (training-pipeline / consolidator / etc. rules)
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine
            engine = get_sql_engine()
            with engine.connect() as conn:
                conn.execute(text(
                    "DELETE FROM public.dash_rules_db WHERE project_slug = :s AND rule_id = :rid"
                ), {"s": slug, "rid": rule_id})
                conn.commit()
        except Exception:
            pass

    _reindex_rules(slug)
    return {"status": "ok", "deleted": rule_id}


@router.post("/{slug}/rules/ai-suggest")
def rules_ai_suggest(slug: str, payload: dict, request: Request):
    """Given a draft calculation/expression, return a suggested name/type/definition.

    Frontend formula-builder uses this to pre-fill the new-rule form. Falls
    back to a deterministic default if the LLM is unavailable so the UI never
    blocks.
    """
    user = _get_user(request)
    _check_access(user, slug)
    payload = payload or {}
    table = (payload.get("table") or "").strip()
    expr = (payload.get("expression") or "").strip()
    cols = payload.get("columns") or []
    op = payload.get("op") or "+"
    default_def = expr or (f" {op} ".join(str(c) for c in cols) if cols else "")
    fallback = {
        "name": f"Calculation: {default_def[:48]}" if default_def else "New Calculation",
        "type": "calculation",
        "definition": default_def or "",
    }
    try:
        from dash.settings import training_llm_call
        prompt = (
            "You name analytics calculations for a business user. "
            f"Table: {table or 'unknown'}. Columns: {cols}. Operator: {op}. "
            f"Expression: {default_def or '(none)'}. "
            "Return ONLY compact JSON with keys: name (<=48 chars, sentence case), "
            "type (one of calculation|metric|kpi), definition (one-line explanation)."
        )
        resp = training_llm_call(prompt, task="extraction")
        if resp:
            import re
            txt = resp.strip()
            # Strip markdown fences if present
            m = re.search(r"\{.*\}", txt, re.DOTALL)
            if m:
                txt = m.group(0)
            try:
                d = json.loads(txt)
                return {
                    "name": (d.get("name") or fallback["name"])[:96],
                    "type": d.get("type") or fallback["type"],
                    "definition": d.get("definition") or fallback["definition"],
                }
            except Exception:
                pass
    except Exception:
        pass
    return fallback


@router.post("/{slug}/rules/import/{source_slug}/{rule_id}")
def import_rule(slug: str, source_slug: str, rule_id: str, request: Request):
    """Copy a rule from another project."""
    user = _get_user(request)
    _check_access(user, slug)
    _check_access(user, source_slug)
    source_file = KNOWLEDGE_DIR / source_slug / "rules" / f"{rule_id}.json"
    if not source_file.exists():
        from fastapi import HTTPException
        raise HTTPException(404, "Source rule not found")

    import time
    with open(source_file) as f:
        rule = json.load(f)

    new_id = f"rule_imported_{int(time.time() * 1000)}"
    rule["id"] = new_id
    rule["source"] = f"imported from {source_slug}"
    rule["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    dest_dir = _rules_dir(slug)
    with open(dest_dir / f"{new_id}.json", "w") as f:
        json.dump(rule, f, indent=2)

    _reindex_rules(slug)
    return {"status": "ok", "rule": rule}


def _reindex_rules(slug: str):
    """Re-index project rules into PgVector knowledge."""
    try:
        from db.session import create_project_knowledge
        knowledge = create_project_knowledge(slug)
        rules_dir = KNOWLEDGE_DIR / slug / "rules"
        if rules_dir.exists():
            files = [f for f in rules_dir.iterdir() if f.is_file() and f.suffix == ".json"]
            if files:
                knowledge.load_documents(files)
    except Exception:
        pass
