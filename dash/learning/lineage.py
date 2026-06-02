"""Hypothesis lineage — track which prior hypotheses spawned which new ones.

kpt's "diff-as-experiment" pattern: every iteration is a child of
some parent experiment. Builds a tree-of-experiments queryable via API.
"""
from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_lineage(hypothesis_id: int, dash_engine=None) -> dict:
    """Return {ancestors: [...], descendants: [...], self: {...}}.

    Walks parent chain up + child tree down.
    """
    out: dict = {"self": None, "ancestors": [], "descendants": [], "depth": 0}
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            # Self
            r = conn.execute(text(
                "SELECT id, statement, hypothesis_type, confidence, "
                " verification_status, parent_hypothesis_id, lineage_depth "
                "FROM public.dash_hypotheses WHERE id = :id"
            ), {"id": hypothesis_id}).fetchone()
            if not r:
                return out
            out["self"] = _row_to_dict(r)
            out["depth"] = int(r[6] or 0)

            # Ancestors (walk up)
            current_parent = r[5]
            while current_parent:
                r2 = conn.execute(text(
                    "SELECT id, statement, hypothesis_type, confidence, "
                    " verification_status, parent_hypothesis_id, lineage_depth "
                    "FROM public.dash_hypotheses WHERE id = :id"
                ), {"id": current_parent}).fetchone()
                if not r2:
                    break
                out["ancestors"].append(_row_to_dict(r2))
                current_parent = r2[5]
                if len(out["ancestors"]) > 20:
                    break

            # Descendants (recursive CTE)
            descendants = conn.execute(text(
                "WITH RECURSIVE tree AS ("
                " SELECT id, statement, hypothesis_type, confidence, "
                "        verification_status, parent_hypothesis_id, "
                "        lineage_depth, 1 AS gen "
                " FROM public.dash_hypotheses "
                " WHERE parent_hypothesis_id = :id "
                " UNION ALL "
                " SELECT h.id, h.statement, h.hypothesis_type, h.confidence, "
                "        h.verification_status, h.parent_hypothesis_id, "
                "        h.lineage_depth, t.gen + 1 "
                " FROM public.dash_hypotheses h "
                " JOIN tree t ON h.parent_hypothesis_id = t.id "
                " WHERE t.gen < 5 "
                ") SELECT * FROM tree ORDER BY gen, id LIMIT 100"
            ), {"id": hypothesis_id}).fetchall()

            for d in descendants:
                out["descendants"].append({
                    **_row_to_dict(d),
                    "generation": int(d[7] or 1),
                })
    except Exception as e:
        logger.warning(f"get_lineage failed: {e}")
    return out


def get_root_trees(project_slug: str, limit: int = 20,
                   dash_engine=None) -> list[dict]:
    """List root hypotheses (no parent) for a project, with descendant counts."""
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT h.id, h.statement, h.confidence, h.verification_status, "
                " (SELECT COUNT(*) FROM public.dash_hypotheses h2 "
                "    WHERE h2.parent_hypothesis_id = h.id) AS child_count "
                "FROM public.dash_hypotheses h "
                "WHERE h.project_slug = :s "
                "  AND h.parent_hypothesis_id IS NULL "
                "ORDER BY h.created_at DESC LIMIT :n"
            ), {"s": project_slug, "n": limit}).fetchall()
        return [{
            "id": r[0], "statement": (r[1] or "")[:200],
            "confidence": float(r[2] or 0),
            "status": r[3], "child_count": int(r[4] or 0),
        } for r in rows]
    except Exception as e:
        logger.warning(f"get_root_trees failed: {e}")
        return []


def _row_to_dict(r) -> dict:
    return {
        "id": r[0], "statement": (r[1] or "")[:300],
        "type": r[2], "confidence": float(r[3] or 0),
        "status": r[4], "parent_id": r[5],
        "depth": int(r[6] or 0),
    }
