"""Federation smoke test endpoint."""
import logging
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/connectors", tags=["federation_test"])


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401)
    return user


@router.post("/sources/{source_id}/test-federation")
async def test_federation(source_id: int, request: Request):
    user = _get_user(request)

    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        with get_sql_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT project_slug FROM public.dash_data_sources WHERE id = :id"
            ), {"id": source_id}).fetchone()
        if not row:
            raise HTTPException(404, "source not found")
        project_slug = row[0]
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "message": f"db error: {e}"}

    # Get providers in project
    try:
        from dash.providers import get_registry
        providers = get_registry().list_for_project(project_slug)
    except Exception as e:
        return {"ok": False, "message": f"registry: {e}"}

    if len(providers) < 2:
        return {
            "ok": False,
            "message": f"Need 2+ sources for federation test; have {len(providers)}",
        }

    # Build sample query: pick first table from each
    p1 = providers[0]
    p2 = providers[1]
    t1 = _first_table(p1)
    t2 = _first_table(p2)
    if not (t1 and t2):
        return {
            "ok": False,
            "message": "could not find sample tables in both sources",
            "sample_sql": "",
        }

    sample_sql = (f"SELECT a.*, b.* "
                  f"FROM {p1.id}.{t1} a, {p2.id}.{t2} b "
                  f"LIMIT 5")

    # Run through parser to verify federation detection works
    try:
        from dash.providers.federation.parser import parse
        parsed = parse(sample_sql)
        if not parsed.is_federated:
            return {"ok": False, "message": "parser did not detect federation",
                    "sample_sql": sample_sql}
        return {
            "ok": True,
            "message": (f"Federation parser detected {len(parsed.provider_ids)} sources. "
                        f"Tables resolvable. Run via Analyst federated_query()."),
            "sample_sql": sample_sql,
            "providers": list(parsed.provider_ids),
        }
    except Exception as e:
        return {"ok": False, "message": f"parser: {e}", "sample_sql": sample_sql}


def _first_table(provider) -> str:
    schema = getattr(provider, "schema_blob", None) or {}
    tables = schema.get("tables", [])
    if not tables:
        return ""
    t = tables[0]
    if isinstance(t, str):
        return t
    if isinstance(t, dict):
        return t.get("name") or t.get("table_name", "")
    return ""
