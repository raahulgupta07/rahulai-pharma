"""Agent IQ — composite growth metric tracking how 'smart' an agent has become.

Computed at end of each LearningCycle. Persisted to
dash_self_learning_runs.metadata under key 'agent_iq'.

Score formula:
    iq = log10(1 + active_memories) * avg_conf * kg_density * persona_richness * 100

Range: 0 (just-born project) → ~500-1000 (well-trained, weeks of self-learning).
"""
from __future__ import annotations
import json
import logging
import math
from dataclasses import dataclass, asdict, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class IQSnapshot:
    project_slug: Optional[str]
    active_memories: int
    avg_confidence: float
    kg_density: float
    persona_richness: float
    agent_iq: float
    components: dict = field(default_factory=dict)


def compute(project_slug: Optional[str], dash_engine=None) -> IQSnapshot:
    """Compute agent_iq for a project (or central if None)."""
    snap = IQSnapshot(
        project_slug=project_slug,
        active_memories=0,
        avg_confidence=0.0,
        kg_density=0.0,
        persona_richness=0.0,
        agent_iq=0.0,
        components={},
    )

    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()

        with eng.connect() as conn:
            # 1. Active memories + avg confidence
            r = conn.execute(text(
                "SELECT COUNT(*) AS n, COALESCE(AVG(confidence_score), 0.5) AS conf "
                "FROM public.dash_memories "
                "WHERE (archived IS NULL OR archived = FALSE) "
                "  AND COALESCE(confidence_score, 0.5) >= 0.3 "
                "  AND (project_slug = :s OR (:s IS NULL AND project_slug IS NULL))"
            ), {"s": project_slug}).fetchone()
            if r:
                snap.active_memories = int(r[0] or 0)
                snap.avg_confidence = float(r[1] or 0)

            # 2. KG density: distinct entities / threshold
            r2 = conn.execute(text(
                "WITH ent AS ("
                "  SELECT DISTINCT subject AS e FROM public.dash_knowledge_triples "
                "  WHERE project_slug = :s OR (:s IS NULL AND project_slug IS NULL) "
                "  UNION "
                "  SELECT DISTINCT object AS e FROM public.dash_knowledge_triples "
                "  WHERE project_slug = :s OR (:s IS NULL AND project_slug IS NULL) "
                ") SELECT COUNT(*) FROM ent"
            ), {"s": project_slug}).fetchone()
            entity_count = int((r2 or [0])[0] or 0)
            snap.kg_density = min(1.0, entity_count / 200.0)  # 200 entities = max density

            # 3. Persona richness
            r3 = conn.execute(text(
                "SELECT LENGTH(COALESCE(persona, '')) "
                "FROM public.dash_personas "
                "WHERE project_slug = :s "
                "ORDER BY created_at DESC LIMIT 1"
            ), {"s": project_slug}).fetchone()
            persona_len = int((r3 or [0])[0] or 0)
            snap.persona_richness = min(1.0, persona_len / 2000.0)

        # 4. Composite
        if snap.active_memories > 0:
            snap.agent_iq = (
                math.log10(1 + snap.active_memories)
                * max(0.1, snap.avg_confidence)
                * max(0.1, snap.kg_density)
                * max(0.1, snap.persona_richness)
                * 100.0
            )

        snap.components = {
            "active_memories": snap.active_memories,
            "avg_confidence": round(snap.avg_confidence, 3),
            "kg_density": round(snap.kg_density, 3),
            "persona_richness": round(snap.persona_richness, 3),
            "agent_iq": round(snap.agent_iq, 2),
        }
    except Exception as e:
        logger.warning(f"agent_iq compute failed: {e}")

    return snap


def persist_to_run(run_id: int, snap: IQSnapshot, dash_engine=None) -> bool:
    """Add agent_iq snapshot to dash_self_learning_runs.metadata JSONB."""
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_self_learning_runs "
                "SET metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:iq AS jsonb) "
                "WHERE id = :id"
            ), {"iq": json.dumps({"agent_iq": asdict(snap)}), "id": run_id})
            conn.commit()
        return True
    except Exception as e:
        logger.warning(f"persist_to_run failed: {e}")
        return False


def history(project_slug: Optional[str], days: int = 30,
            dash_engine=None) -> list[dict]:
    """Return chronological list of agent_iq snapshots for charting."""
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT cycle_num, started_at, metadata "
                "FROM public.dash_self_learning_runs "
                "WHERE (project_slug = :s OR (:s IS NULL AND project_slug IS NULL)) "
                "  AND started_at > NOW() - (:d * INTERVAL '1 day') "
                "  AND status = 'completed' "
                "ORDER BY started_at ASC"
            ), {"s": project_slug, "d": days}).fetchall()
        out = []
        for r in rows:
            md = r[2] or {}
            iq_data = (md or {}).get("agent_iq") or {}
            out.append({
                "cycle_num": r[0],
                "ts": r[1].isoformat() if r[1] else None,
                "agent_iq": iq_data.get("agent_iq", 0),
                "components": iq_data.get("components", {}),
            })
        return out
    except Exception as e:
        logger.warning(f"agent_iq history failed: {e}")
        return []
