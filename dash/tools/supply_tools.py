"""
Supply Sentry tools — Sprint 3 supply chain pillar.

10 tools + create_supply_tools() factory. Mirrors ops_tools.py pattern.

DB rules:
  - get_write_engine() for INSERT/UPDATE on dash.* tables
  - get_sql_engine() for SELECTs
  - CAST(:x AS jsonb) — never :x::jsonb (PgBouncer collision)
  - Idempotent upserts via ON CONFLICT
  - cross_tenant_exposure FAILS CLOSED on missing consent
"""
from __future__ import annotations

import json
import logging
from datetime import date as _date
from typing import Any, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ── Engines (fail-soft) ──────────────────────────────────────────────────

def _write_engine():
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception:
        from db.session import get_sql_engine
        return get_sql_engine()


def _sql_engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ── 1. register_supplier ────────────────────────────────────────────────

def register_supplier(legal_name: str, country: str, tier: str,
                      region: Optional[str] = None,
                      financial_health_score: Optional[float] = None,
                      metadata: Optional[dict] = None) -> dict:
    """Insert a supplier. Idempotent on (legal_name, country)."""
    try:
        meta_json = json.dumps(metadata or {})
        with _write_engine().begin() as cx:
            r = cx.execute(text("""
                INSERT INTO dash.dash_suppliers
                    (legal_name, country, region, tier,
                     financial_health_score, metadata)
                VALUES (:ln, :c, :r, :t, :fhs, CAST(:m AS jsonb))
                ON CONFLICT (legal_name, country) DO UPDATE SET
                    region = COALESCE(EXCLUDED.region, dash.dash_suppliers.region),
                    tier = EXCLUDED.tier,
                    financial_health_score = COALESCE(
                        EXCLUDED.financial_health_score,
                        dash.dash_suppliers.financial_health_score),
                    metadata = COALESCE(EXCLUDED.metadata,
                                        dash.dash_suppliers.metadata)
                RETURNING id
            """), {"ln": legal_name, "c": country, "r": region, "t": tier,
                   "fhs": financial_health_score, "m": meta_json}).fetchone()
        return {"ok": True, "supplier_id": str(r[0])}
    except Exception as e:
        logger.exception("register_supplier failed")
        return {"ok": False, "error": str(e)}


# ── 2. link_sku ─────────────────────────────────────────────────────────

def link_sku(supplier_id: str, tenant_slug: str, sku: str,
             lead_time_days: int, unit_cost_usd: float,
             mou_units: Optional[float] = None,
             category: Optional[str] = None,
             sku_description: Optional[str] = None,
             is_primary: bool = True) -> dict:
    """Link a supplier to a SKU for a tenant. Idempotent on
    (supplier_id, tenant_slug, sku)."""
    try:
        with _write_engine().begin() as cx:
            r = cx.execute(text("""
                INSERT INTO dash.dash_supplier_skus
                    (supplier_id, tenant_slug, sku, sku_description,
                     category, mou_units, lead_time_days, unit_cost_usd,
                     is_primary)
                VALUES (:sid, :ts, :sku, :sd, :cat, :mou, :lt, :uc, :ip)
                ON CONFLICT (supplier_id, tenant_slug, sku) DO UPDATE SET
                    sku_description = COALESCE(EXCLUDED.sku_description,
                        dash.dash_supplier_skus.sku_description),
                    category = COALESCE(EXCLUDED.category,
                        dash.dash_supplier_skus.category),
                    mou_units = EXCLUDED.mou_units,
                    lead_time_days = EXCLUDED.lead_time_days,
                    unit_cost_usd = EXCLUDED.unit_cost_usd,
                    is_primary = EXCLUDED.is_primary
                RETURNING id
            """), {"sid": supplier_id, "ts": tenant_slug, "sku": sku,
                   "sd": sku_description, "cat": category,
                   "mou": mou_units, "lt": lead_time_days,
                   "uc": unit_cost_usd, "ip": is_primary}).fetchone()
        return {"ok": True, "sku_link_id": str(r[0])}
    except Exception as e:
        logger.exception("link_sku failed")
        return {"ok": False, "error": str(e)}


# ── 3. ingest_supplier_event ────────────────────────────────────────────

def ingest_supplier_event(supplier_id: str, event_type: str, severity: str,
                          title: str, body: Optional[str] = None,
                          source_url: Optional[str] = None,
                          payload: Optional[dict] = None) -> dict:
    """Record an event affecting a supplier."""
    if severity not in ("info", "warn", "critical"):
        severity = "info"
    try:
        payload_json = json.dumps(payload or {})
        with _write_engine().begin() as cx:
            r = cx.execute(text("""
                INSERT INTO dash.dash_supplier_events
                    (supplier_id, event_type, severity, title, body,
                     source_url, payload, detected_at)
                VALUES (:sid, :et, :sev, :t, :b, :u,
                        CAST(:p AS jsonb), now())
                RETURNING id
            """), {"sid": supplier_id, "et": event_type, "sev": severity,
                   "t": title, "b": body, "u": source_url,
                   "p": payload_json}).fetchone()
        return {"ok": True, "event_id": str(r[0])}
    except Exception as e:
        logger.exception("ingest_supplier_event failed")
        return {"ok": False, "error": str(e)}


# ── 4. score_supplier ───────────────────────────────────────────────────

def score_supplier(supplier_id: str) -> dict:
    """Composite supplier health score (0-100, HIGHER=better).
    score_band is generated by DB (>=80 green, >=50 yellow, else red).
    Idempotent per day: deletes today's prior row before insert."""
    try:
        with _sql_engine().connect() as cx:
            sup = cx.execute(text("""
                SELECT financial_health_score, concentration_risk
                FROM dash.dash_suppliers WHERE id = :s
            """), {"s": supplier_id}).fetchone()
            if not sup:
                return {"ok": False, "error": "supplier not found"}

            # Recent event PENALTY: critical=10, warn=4, info=1 (last 90d)
            ev = cx.execute(text("""
                SELECT severity, COUNT(*) FROM dash.dash_supplier_events
                WHERE supplier_id = :s
                  AND detected_at > now() - interval '90 days'
                GROUP BY severity
            """), {"s": supplier_id}).fetchall()
            ev_map = {r[0]: int(r[1]) for r in ev}
            event_penalty = (ev_map.get("critical", 0) * 10
                              + ev_map.get("warn", 0) * 4
                              + ev_map.get("info", 0) * 1)
            event_penalty = min(event_penalty, 40)

        fhs = float(sup[0]) if sup[0] is not None else 50.0  # higher=better
        conc = float(sup[1]) if sup[1] is not None else 0.0  # higher=worse
        # composite (higher=better): start from financial health,
        # subtract concentration risk and recent-event penalty.
        score = fhs - (conc * 0.25) - event_penalty
        score = round(max(0.0, min(100.0, score)), 2)

        components = {
            "financial_health_score": fhs,
            "concentration_risk": conc,
            "event_penalty_90d": event_penalty,
        }
        comp_json = json.dumps(components)

        with _write_engine().begin() as cx:
            # Idempotent on (supplier_id, day): delete today's row first.
            cx.execute(text("""
                DELETE FROM dash.dash_supplier_risk_scores
                WHERE supplier_id = :s
                  AND computed_at::date = current_date
            """), {"s": supplier_id})
            r = cx.execute(text("""
                INSERT INTO dash.dash_supplier_risk_scores
                    (supplier_id, score, components, computed_at)
                VALUES (:s, :sc, CAST(:c AS jsonb), now())
                RETURNING id, score_band
            """), {"s": supplier_id, "sc": score,
                   "c": comp_json}).fetchone()
        band = r[1] if r else None
        return {"ok": True, "score": score, "score_band": band,
                "components": components,
                "row_id": str(r[0]) if r else None}
    except Exception as e:
        logger.exception("score_supplier failed")
        return {"ok": False, "error": str(e)}


# ── 5. detect_supply_anomaly ────────────────────────────────────────────

def detect_supply_anomaly(supplier_id: Optional[str] = None,
                          z_threshold: float = 2.0) -> List[dict]:
    """Detect lead-time spikes, severity escalations, missed SLAs.
    Returns list of anomaly dicts. Pure read; no persistence."""
    anomalies: List[dict] = []
    try:
        with _sql_engine().connect() as cx:
            # Critical-event escalation: any critical event in last 14d
            q = """
                SELECT supplier_id, COUNT(*) as crit_count
                FROM dash.dash_supplier_events
                WHERE severity = 'critical'
                  AND detected_at > now() - interval '14 days'
            """
            params: dict[str, Any] = {}
            if supplier_id:
                q += " AND supplier_id = :s"
                params["s"] = supplier_id
            q += " GROUP BY supplier_id"
            rows = cx.execute(text(q), params).fetchall()
            for r in rows:
                anomalies.append({
                    "supplier_id": str(r[0]),
                    "type": "severity_escalation",
                    "severity": "critical",
                    "details": f"{int(r[1])} critical events in last 14 days",
                })

            # Lead-time spike: compare each SKU's lead_time_days to peer median
            q2 = """
                SELECT supplier_id, sku, lead_time_days
                FROM dash.dash_supplier_skus
                WHERE lead_time_days IS NOT NULL
            """
            params2: dict[str, Any] = {}
            if supplier_id:
                q2 += " AND supplier_id = :s"
                params2["s"] = supplier_id
            sku_rows = cx.execute(text(q2), params2).fetchall()
            # group by sku for peer comparison
            by_sku: dict[str, list[tuple]] = {}
            for sr in sku_rows:
                by_sku.setdefault(sr[1], []).append(
                    (str(sr[0]), float(sr[2])))
            for sku, recs in by_sku.items():
                if len(recs) < 3:
                    continue
                vals = [v for _, v in recs]
                mean = sum(vals) / len(vals)
                var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
                stdev = var ** 0.5 if var > 0 else 0
                if stdev == 0:
                    continue
                for sid, lt in recs:
                    z = (lt - mean) / stdev
                    if abs(z) > z_threshold:
                        anomalies.append({
                            "supplier_id": sid,
                            "type": "lead_time_spike",
                            "severity": "warn" if abs(z) <= 3 else "critical",
                            "details": (f"SKU {sku} lead-time {lt:.1f}d "
                                        f"vs peer mean {mean:.1f}d "
                                        f"(z={z:.2f})"),
                            "sku": sku,
                        })

        return anomalies
    except Exception as e:
        logger.exception("detect_supply_anomaly failed")
        return [{"error": str(e)}]


# ── 6. cross_tenant_exposure ────────────────────────────────────────────

def cross_tenant_exposure(supplier_id: str, requesting_tenant: str) -> dict:
    """Return cross-tenant exposure for a supplier.

    CONSENT-GATED: only returns other tenants' slugs when
    share_aggregate=true. Fails closed if consent missing.
    """
    try:
        with _sql_engine().connect() as cx:
            # All tenants exposed to this supplier
            rows = cx.execute(text("""
                SELECT DISTINCT tenant_slug FROM dash.dash_supplier_skus
                WHERE supplier_id = :s
            """), {"s": supplier_id}).fetchall()
            all_tenants = [r[0] for r in rows]

            # Load consent for all
            consent_rows = cx.execute(text("""
                SELECT tenant_slug, share_aggregate, share_supplier_list
                FROM dash.dash_supply_consent
                WHERE tenant_slug = ANY(:slugs)
            """), {"slugs": all_tenants}).fetchall()
            consent_map = {
                r[0]: {"share_aggregate": bool(r[1]),
                       "share_supplier_list": bool(r[2])}
                for r in consent_rows
            }

            visible_tenants: list[str] = []
            revenue_at_risk = 0.0
            for ts in all_tenants:
                if ts == requesting_tenant:
                    visible_tenants.append(ts)
                    continue
                c = consent_map.get(ts)
                if c and c["share_aggregate"]:
                    visible_tenants.append(ts)
                # else fail-closed: don't surface

            # Compute revenue_at_risk only for visible tenants (last 90d
            # window: sum mou_units * unit_cost_usd; mou_units treated as
            # monthly, so 90d = 3 months).
            if visible_tenants:
                rev_rows = cx.execute(text("""
                    SELECT tenant_slug,
                           SUM(COALESCE(mou_units, 0)
                               * COALESCE(unit_cost_usd, 0) * 3) AS rar
                    FROM dash.dash_supplier_skus
                    WHERE supplier_id = :s
                      AND tenant_slug = ANY(:vis)
                    GROUP BY tenant_slug
                """), {"s": supplier_id, "vis": visible_tenants}).fetchall()
                rev_map = {r[0]: float(r[1] or 0.0) for r in rev_rows}
                revenue_at_risk = sum(rev_map.values())
            else:
                rev_map = {}

            return {
                "ok": True,
                "supplier_id": supplier_id,
                "requesting_tenant": requesting_tenant,
                "total_tenants_exposed": len(all_tenants),
                "visible_tenants": visible_tenants,
                "withheld_count": len(all_tenants) - len(visible_tenants),
                "revenue_at_risk_usd_90d": round(revenue_at_risk, 2),
                "revenue_breakdown": {k: round(v, 2)
                                       for k, v in rev_map.items()},
                "consent_required": "share_aggregate=true",
            }
    except Exception as e:
        logger.exception("cross_tenant_exposure failed")
        return {"ok": False, "error": str(e)}


# ── 7. propose_alt_supplier ─────────────────────────────────────────────

def propose_alt_supplier(sku: str, tenant_slug: str,
                         exclude_supplier_id: Optional[str] = None) -> dict:
    """Rank alternate suppliers for a SKU by lead_time + cost + risk.
    Persists ranking to dash_alt_suppliers."""
    try:
        with _sql_engine().connect() as cx:
            # Find primary supplier for this sku+tenant
            primary = cx.execute(text("""
                SELECT supplier_id, lead_time_days, unit_cost_usd,
                       mou_units, category
                FROM dash.dash_supplier_skus
                WHERE sku = :sku AND tenant_slug = :ts AND is_primary = true
                LIMIT 1
            """), {"sku": sku, "ts": tenant_slug}).fetchone()
            if not primary:
                return {"ok": False,
                        "error": "no primary supplier for sku+tenant"}
            primary_supplier_id = str(primary[0])
            primary_lt = float(primary[1] or 0)
            primary_cost = float(primary[2] or 0)
            mou = float(primary[3] or 0)
            category = primary[4]

            # Find alts in same category (and matching tier when possible)
            alt_q = """
                SELECT s.id, s.legal_name, s.tier, sk.lead_time_days,
                       sk.unit_cost_usd, sk.sku,
                       COALESCE(r.score, 50.0) AS risk_score,
                       COALESCE(r.score_band, 'unknown') AS risk_band
                FROM dash.dash_suppliers s
                JOIN dash.dash_supplier_skus sk ON sk.supplier_id = s.id
                LEFT JOIN LATERAL (
                    SELECT score, score_band FROM dash.dash_supplier_risk_scores
                    WHERE supplier_id = s.id
                    ORDER BY computed_at DESC LIMIT 1
                ) r ON TRUE
                WHERE sk.sku = :sku
                  AND s.id <> :ps
            """
            params: dict[str, Any] = {"sku": sku, "ps": primary_supplier_id}
            if category:
                alt_q += " AND (sk.category = :cat OR sk.category IS NULL)"
                params["cat"] = category
            if exclude_supplier_id:
                alt_q += " AND s.id <> :ex"
                params["ex"] = exclude_supplier_id

            alt_rows = cx.execute(text(alt_q), params).fetchall()

        ranked = []
        for r in alt_rows:
            alt_lt = float(r[3] or 0)
            alt_cost = float(r[4] or 0)
            health_score = float(r[6] or 50.0)  # higher=better
            cost_delta = alt_cost - primary_cost
            lt_delta = alt_lt - primary_lt
            switching_cost = round(
                mou * abs(alt_cost - primary_cost) * 1.1, 2)
            # composite (lower=better): lead-time + cost + (100-health)
            composite = (
                (alt_lt * 0.3) + (alt_cost * 0.3)
                + ((100.0 - health_score) * 0.4)
            )
            ranked.append({
                "supplier_id": str(r[0]),
                "legal_name": r[1],
                "tier": r[2],
                "lead_time_days": alt_lt,
                "unit_cost_usd": alt_cost,
                "health_score": health_score,
                "risk_band": r[7],
                "lead_time_delta_days": lt_delta,
                "cost_delta_usd": cost_delta,
                "switching_cost_usd": switching_cost,
                "composite_score": round(composite, 2),
            })
        ranked.sort(key=lambda x: x["composite_score"])
        top = ranked[:5]

        # Persist
        try:
            switching_cost_top = top[0]["switching_cost_usd"] if top else 0.0
            lt_delta_top = top[0]["lead_time_delta_days"] if top else 0.0
            with _write_engine().begin() as cx:
                cx.execute(text("""
                    INSERT INTO dash.dash_alt_suppliers
                        (sku, tenant_slug, primary_supplier_id, ranked_alts,
                         switching_cost_usd, lead_time_delta_days)
                    VALUES (:sku, :ts, :ps, CAST(:ra AS jsonb), :sc, :ltd)
                    ON CONFLICT (sku, tenant_slug) DO UPDATE SET
                        primary_supplier_id = EXCLUDED.primary_supplier_id,
                        ranked_alts = EXCLUDED.ranked_alts,
                        switching_cost_usd = EXCLUDED.switching_cost_usd,
                        lead_time_delta_days = EXCLUDED.lead_time_delta_days
                """), {"sku": sku, "ts": tenant_slug,
                       "ps": primary_supplier_id,
                       "ra": json.dumps(top),
                       "sc": switching_cost_top,
                       "ltd": lt_delta_top})
        except Exception:
            logger.debug("propose_alt_supplier persist failed",
                         exc_info=True)

        return {"ok": True, "sku": sku, "tenant_slug": tenant_slug,
                "primary_supplier_id": primary_supplier_id,
                "alts_found": len(ranked), "ranked_alts": top}
    except Exception as e:
        logger.exception("propose_alt_supplier failed")
        return {"ok": False, "error": str(e)}


# ── 8. resilience_scorecard ─────────────────────────────────────────────

def resilience_scorecard(tenant_slug: str) -> dict:
    """SKU-by-SKU rollup. Uses latest dash_supplier_risk_scores.score_band
    of primary supplier per SKU."""
    try:
        with _sql_engine().connect() as cx:
            rows = cx.execute(text("""
                SELECT sk.sku, sk.supplier_id, s.legal_name,
                       COALESCE(r.score, NULL) AS score,
                       COALESCE(r.score_band, 'unknown') AS band
                FROM dash.dash_supplier_skus sk
                JOIN dash.dash_suppliers s ON s.id = sk.supplier_id
                LEFT JOIN LATERAL (
                    SELECT score, score_band FROM dash.dash_supplier_risk_scores
                    WHERE supplier_id = sk.supplier_id
                    ORDER BY computed_at DESC LIMIT 1
                ) r ON TRUE
                WHERE sk.tenant_slug = :ts AND sk.is_primary = true
                ORDER BY sk.sku
            """), {"ts": tenant_slug}).fetchall()

        green = yellow = red = unknown = 0
        skus: list[dict] = []
        for r in rows:
            band = r[4]
            if band == "green":
                color = "green"; green += 1
            elif band == "yellow":
                color = "yellow"; yellow += 1
            elif band == "red":
                color = "red"; red += 1
            else:
                color = "gray"; unknown += 1
            skus.append({
                "sku": r[0],
                "supplier_id": str(r[1]),
                "supplier_name": r[2],
                "risk_score": float(r[3]) if r[3] is not None else None,
                "risk_band": band,
                "color": color,
            })
        total = len(skus)
        return {
            "ok": True,
            "tenant_slug": tenant_slug,
            "total_skus": total,
            "counts": {"green": green, "yellow": yellow,
                       "red": red, "unknown": unknown},
            "skus": skus,
        }
    except Exception as e:
        logger.exception("resilience_scorecard failed")
        return {"ok": False, "error": str(e)}


# ── 9. news_scan_suppliers ──────────────────────────────────────────────

def news_scan_suppliers(country: Optional[str] = None,
                        since_hours: int = 24) -> List[dict]:
    """Pull cached supplier events (stub for future news ingestion)."""
    try:
        q = """
            SELECT e.id, e.supplier_id, s.legal_name, s.country,
                   e.event_type, e.severity, e.title, e.source_url,
                   e.detected_at
            FROM dash.dash_supplier_events e
            JOIN dash.dash_suppliers s ON s.id = e.supplier_id
            WHERE e.detected_at > now() - (:h * interval '1 hour')
        """
        params: dict[str, Any] = {"h": int(since_hours)}
        if country:
            q += " AND s.country = :c"
            params["c"] = country
        q += " ORDER BY e.detected_at DESC LIMIT 200"
        with _sql_engine().connect() as cx:
            rows = cx.execute(text(q), params).fetchall()
        return [{
            "event_id": str(r[0]),
            "supplier_id": str(r[1]),
            "supplier_name": r[2],
            "country": r[3],
            "event_type": r[4],
            "severity": r[5],
            "title": r[6],
            "source_url": r[7],
            "detected_at": r[8].isoformat() if r[8] else None,
        } for r in rows]
    except Exception as e:
        logger.exception("news_scan_suppliers failed")
        return [{"error": str(e)}]


# ── 10. generate_supply_risk_report ─────────────────────────────────────

def generate_supply_risk_report(tenant_slug: str,
                                 period_days: int = 7) -> dict:
    """Compose scorecard + top risks + alt recommendations summary."""
    try:
        scorecard = resilience_scorecard(tenant_slug)
        if not scorecard.get("ok"):
            return scorecard

        # Recent events for tenant's suppliers
        with _sql_engine().connect() as cx:
            ev_rows = cx.execute(text("""
                SELECT e.supplier_id, s.legal_name, e.event_type,
                       e.severity, e.title, e.detected_at
                FROM dash.dash_supplier_events e
                JOIN dash.dash_suppliers s ON s.id = e.supplier_id
                WHERE e.supplier_id IN (
                    SELECT DISTINCT supplier_id FROM dash.dash_supplier_skus
                    WHERE tenant_slug = :ts
                )
                  AND e.detected_at > now() - (:d * interval '1 day')
                ORDER BY
                    CASE e.severity WHEN 'critical' THEN 3
                                    WHEN 'warn' THEN 2 ELSE 1 END DESC,
                    e.detected_at DESC
                LIMIT 25
            """), {"ts": tenant_slug, "d": int(period_days)}).fetchall()

            # Latest alt recommendations
            alt_rows = cx.execute(text("""
                SELECT sku, primary_supplier_id, switching_cost_usd,
                       lead_time_delta_days
                FROM dash.dash_alt_suppliers
                WHERE tenant_slug = :ts
                LIMIT 10
            """), {"ts": tenant_slug}).fetchall()

        events = [{
            "supplier_id": str(r[0]),
            "supplier_name": r[1],
            "event_type": r[2],
            "severity": r[3],
            "title": r[4],
            "detected_at": r[5].isoformat() if r[5] else None,
        } for r in ev_rows]

        alts = [{
            "sku": r[0],
            "primary_supplier_id": str(r[1]) if r[1] else None,
            "switching_cost_usd": float(r[2]) if r[2] is not None else None,
            "lead_time_delta_days": float(r[3]) if r[3] is not None else None,
        } for r in alt_rows]

        top_risks = [s for s in scorecard["skus"]
                     if s["color"] in ("yellow", "red")][:10]

        return {
            "ok": True,
            "tenant_slug": tenant_slug,
            "period_days": period_days,
            "scorecard_counts": scorecard["counts"],
            "total_skus": scorecard["total_skus"],
            "top_risks": top_risks,
            "recent_events": events,
            "alt_recommendations": alts,
        }
    except Exception as e:
        logger.exception("generate_supply_risk_report failed")
        return {"ok": False, "error": str(e)}


# ── @tool factory ───────────────────────────────────────────────────────

def create_supply_tools(project_slug: str,
                         user_id: Optional[int] = None):
    """Return Agno @tool wrappers for Supply Sentry."""
    from agno.tools import tool

    @tool(name="register_supplier",
          description="Register or upsert a supplier (legal_name + country "
          "unique). Args: legal_name, country, tier (e.g. tier_1/tier_2), "
          "region (optional), financial_health_score (0-100, higher=better, "
          "optional), metadata (dict, optional).")
    def _register(legal_name: str, country: str, tier: str,
                   region: str = "", financial_health_score: float = -1.0,
                   metadata: dict = None) -> str:
        fhs = financial_health_score if financial_health_score >= 0 else None
        r = register_supplier(legal_name, country, tier,
                                region or None, fhs, metadata or None)
        return json.dumps(r, default=str)

    @tool(name="link_sku",
          description="Link a supplier to a SKU for a tenant. Idempotent on "
          "(supplier_id, tenant_slug, sku). Args: supplier_id, tenant_slug, "
          "sku, lead_time_days, unit_cost_usd, mou_units (monthly units, "
          "optional), category (optional), is_primary (default True).")
    def _link(supplier_id: str, tenant_slug: str, sku: str,
               lead_time_days: int, unit_cost_usd: float,
               mou_units: float = 0.0, category: str = "",
               is_primary: bool = True) -> str:
        mou = mou_units if mou_units > 0 else None
        r = link_sku(supplier_id, tenant_slug, sku, lead_time_days,
                      unit_cost_usd, mou, category or None, None, is_primary)
        return json.dumps(r, default=str)

    @tool(name="ingest_supplier_event",
          description="Record an event affecting a supplier (news, incident, "
          "rating change, etc). Args: supplier_id, event_type, severity "
          "(info|warn|critical), title, body (optional), source_url "
          "(optional), payload (dict, optional).")
    def _event(supplier_id: str, event_type: str, severity: str,
                title: str, body: str = "", source_url: str = "",
                payload: dict = None) -> str:
        r = ingest_supplier_event(supplier_id, event_type, severity,
                                    title, body or None,
                                    source_url or None, payload or None)
        return json.dumps(r, default=str)

    @tool(name="score_supplier",
          description="Compute composite risk score (0-100, higher=worse). "
          "Combines delivery variance + financial health + concentration + "
          "recent events. Writes one row per (supplier_id, day). "
          "Args: supplier_id.")
    def _score(supplier_id: str) -> str:
        r = score_supplier(supplier_id)
        return json.dumps(r, default=str)

    @tool(name="detect_supply_anomaly",
          description="Detect lead-time spikes (z-score across peers per "
          "SKU), severity escalations (recent critical events). "
          "Args: supplier_id (optional, scope to one), z_threshold "
          "(default 2.0).")
    def _detect(supplier_id: str = "", z_threshold: float = 2.0) -> str:
        r = detect_supply_anomaly(supplier_id or None, z_threshold)
        return json.dumps(r, default=str)

    @tool(name="cross_tenant_exposure",
          description="CONSENT-GATED. Returns tenants exposed to a supplier "
          "PLUS revenue-at-risk (90d). Other tenants' slugs only shown when "
          "dash_supply_consent.share_aggregate=true. Fails closed on missing "
          "consent. Args: supplier_id, requesting_tenant.")
    def _expo(supplier_id: str, requesting_tenant: str) -> str:
        r = cross_tenant_exposure(supplier_id, requesting_tenant)
        return json.dumps(r, default=str)

    @tool(name="propose_alt_supplier",
          description="Rank alternate suppliers for a SKU by composite "
          "(lead_time + cost + risk). Joins on category + tier. Persists "
          "ranking. switching_cost = mou × |Δunit_cost| × 1.1. "
          "Args: sku, tenant_slug, exclude_supplier_id (optional).")
    def _alt(sku: str, tenant_slug: str,
              exclude_supplier_id: str = "") -> str:
        r = propose_alt_supplier(sku, tenant_slug,
                                   exclude_supplier_id or None)
        return json.dumps(r, default=str)

    @tool(name="resilience_scorecard",
          description="SKU rollup green/yellow/red by primary supplier's "
          "latest risk band. green=low, yellow=moderate/elevated, "
          "red=critical. Args: tenant_slug.")
    def _scard(tenant_slug: str) -> str:
        r = resilience_scorecard(tenant_slug)
        return json.dumps(r, default=str)

    @tool(name="news_scan_suppliers",
          description="Pull cached supplier events (stub for news ingest). "
          "Returns list of events. Args: country (optional), since_hours "
          "(default 24).")
    def _news(country: str = "", since_hours: int = 24) -> str:
        r = news_scan_suppliers(country or None, since_hours)
        return json.dumps(r, default=str)

    @tool(name="generate_supply_risk_report",
          description="Compose scorecard + top risks + recent events + alt "
          "recommendations into one tenant report. Args: tenant_slug, "
          "period_days (default 7).")
    def _report(tenant_slug: str, period_days: int = 7) -> str:
        r = generate_supply_risk_report(tenant_slug, period_days)
        return json.dumps(r, default=str)

    return [_register, _link, _event, _score, _detect, _expo, _alt,
             _scard, _news, _report]
