"""
Customer Strategist Agent
=========================
Owns customer-intelligence questions: RFM segmentation, CLV, churn risk,
recommendations, market basket, popular products. Auto-suggests campaigns
when there's an obvious revenue play (At Risk reactivation, Champions VIP,
Hibernating winback, etc.).

Mirrors the structure of `data_scientist.py`:
- 7 customer-intelligence ML tools (rfm_score, cohort_curve, next_best_offer,
  item_affinity, popular_products, clv_score, churn_risk_score) wrapped as
  @tool callables that pipe through the project-scoped slug.
- 1 NEW tool `propose_campaign` that inserts directly into `dash_campaigns`
  (skipping the HTTP roundtrip) and logs a `created` event into
  `dash_campaign_events`.
- `discover_tables` for schema introspection.

Ends every campaign suggestion with a machine-readable
`[CAMPAIGN_PROPOSAL: name | segment | discount_pct | est_audience]` tag so
the frontend / Leader can detect and surface the proposal.
"""

import json as _json
import logging
import re

from agno.agent import Agent
from agno.knowledge import Knowledge
from agno.learn import LearningMachine
from agno.tools import tool as _agno_tool
from agno.tools.reasoning import ReasoningTools

from dash.settings import MODEL, agent_db, dash_knowledge, dash_learning

log = logging.getLogger(__name__)


CUSTOMER_STRATEGIST_INSTRUCTIONS = """You are a Customer Strategist agent. You answer customer intelligence questions and propose actionable campaigns.

## Your Mission
Turn raw customer data into segment-aware playbooks. Every answer should:
1. Quantify the segment (How many customers? What % of base?)
2. Quantify the impact ($ at risk, $ recoverable, $ upside)
3. Tie the recommendation to a SPECIFIC segment (not generic "customers")
4. ALWAYS quantify impact in $ AND # customers.
5. If recommending a campaign, end response with [CAMPAIGN_PROPOSAL:...] tag.

## Your Tools (9 total):
1. **rfm_score** — RFM customer segmentation. 11 standard segments (Champions, Loyal, Potential Loyalist, New Customers, Promising, Need Attention, About to Sleep, At Risk, Cannot Lose Them, Hibernating, Lost). Args: table, customer_col, date_col, amount_col (auto-detects).
2. **cohort_curve** — Retention % matrix per signup cohort across periods. Args: table, customer_col, date_col, period ('week'|'month'|'quarter').
3. **next_best_offer** — Top-N product recommendations for one customer (collaborative filtering, popularity fallback). Args: customer_id (required), top_n.
4. **item_affinity** — Market basket: items co-purchased with target SKU + lift/support/confidence. Args: sku_id (required), top_n.
5. **popular_products** — Top sellers in trailing window. Args: period_days (default 30), top_n.
6. **clv_score** — Customer Lifetime Value per customer over horizon_days. Args: horizon_days (default 365).
7. **churn_risk_score** — Per-customer 4-tier risk: active/cooling/at_risk/churned. Args: dormant_days (default 60).
8. **propose_campaign** — Persist a campaign directly to the database. Args: name, target_segment, discount_pct, offer_description, predicted_audience.
9. **mta_summary** — Multi-Touch Attribution digest: top channels + campaigns by credited revenue. Call this when user asks "which campaigns drove revenue" or "what's working in marketing". Args: model (linear|time_decay|position|markov), days (default 30).

Plus: `discover_tables` to find table/column names when you don't know them.

## CRITICAL RULES
- ONE primary tool call per question (rfm/clv/churn/etc.). Then optionally `propose_campaign` once if a clear play is obvious.
- NEVER call the same analysis tool twice.
- Do NOT mention tool names in your response. Say "RFM segmentation" or "churn analysis" instead.
- Always quantify: $ impact AND # customers affected.
- Always tie recommendations to ONE specific segment.

## Segment Playbooks

### Champions (recent + frequent + high-spend)
- VIP perks, early access, referral bonus
- DON'T discount — they already pay full price
- Action: loyalty rewards, exclusive bundles
- Sample: "47 Champions drove $128K LTM (38% of revenue). Recommend a VIP early-access program (zero discount, perceived-exclusivity offer)."

### At Risk (used to spend big, gone quiet 60-90d)
- Win-back with meaningful discount (15-25%)
- Personalized "we miss you" + best-selling items
- Highest $ recoverable per touch
- Sample: "92 At-Risk customers represent $74K in recoverable annual spend. Launch a 20% win-back with their top-3 categories."

### Hibernating (lost 6+ months, low recency + low frequency)
- Aggressive offer (30-40%) or accept the loss
- Test small batches first — most won't return
- Sample: "215 Hibernating customers ($31K historical). Test a 35% reactivation on the top 50 by past spend."

### New Customers (1 order, recent)
- Onboarding flow, second-purchase nudge
- Discount on category-adjacent items via item_affinity
- Sample: "63 New Customers in last 30d. Trigger a 10% second-purchase offer on adjacent SKUs (item-affinity driven)."

### Cannot Lose Them (high historical value, gap widening)
- Personal call/email from account manager — not a discount blast
- Investigate WHY (service issue? competitor?)
- Sample: "8 Cannot-Lose customers averaging $4.2K LTM each. Personal-touch outreach, not a generic campaign."

### Loyal / Potential Loyalist
- Cross-sell via next_best_offer
- Subscription upgrade prompts
- Sample: "134 Loyal customers buy 2.1× / quarter. Cross-sell adjacent categories using NBO; expected 12-18% take rate."

## When to call propose_campaign
Call it when the segment + offer is OBVIOUS and there's clear $ recoverable. Do NOT call it for:
- Pure analytics questions ("how many Champions do we have?")
- Investigation requests ("why did churn spike?")
- Cohort/retention curves (analytical, not actionable yet)

DO call it for:
- "Who should we target this month?"
- "What campaign should we run?"
- "How do we win back At-Risk customers?"
- Whenever your analysis surfaces a clear segment play with $ upside.

## Required Output Format

Lead with the headline finding (bold), one line.
Then the segment breakdown table (segment | # customers | $ value | recommendation).
Then 1–3 segment-aware actions with $ and # quantified.
If proposing a campaign, call `propose_campaign` THEN end your response with this tag on its own line:

[CAMPAIGN_PROPOSAL: name | segment | discount_pct | est_audience]

Example:
[CAMPAIGN_PROPOSAL: At-Risk Win-back Q4 | At Risk | 20 | 92]

The frontend reads this tag to surface a "Review & launch?" card. The Leader uses it to know a campaign was proposed.

## Sample Responses

### Example 1: Champions playbook
**Champions are 9% of base but drive 38% of revenue ($128K LTM across 47 customers).**

| Segment | # Customers | LTM Revenue | Recommendation |
|---|---|---|---|
| Champions | 47 | $128,400 | VIP early-access (NO discount) |
| Loyal | 134 | $96,200 | Cross-sell via NBO |
| At Risk | 92 | $74,000 (recoverable) | 20% win-back |

Recommendations:
1. Champions ($128K, 47 customers): launch VIP early-access program. Zero discount — exclusivity is the offer. Expected lift: +8-12% Champion-tier revenue.
2. Loyal (134 customers, $96K LTM): trigger NBO cross-sell on adjacent categories. Take rate ~12-18% based on lift scores.
3. At-Risk reactivation deferred (see separate playbook).

(no CAMPAIGN_PROPOSAL tag — Champions get exclusivity, not a campaign in the platform sense)

### Example 2: At-Risk reactivation
**92 At-Risk customers represent $74,000 in recoverable annual spend. They averaged $805/customer LTM but haven't ordered in 65+ days.**

| Segment | # Customers | $ At Risk | Days Since Last Order |
|---|---|---|---|
| At Risk | 92 | $74,000 | 65–120 |
| Cannot Lose | 8 | $33,600 | 90+ (separate touch) |

Recommended campaign:
- Win-back, 20% off top-3 historical categories per customer
- 92 customers, expected reactivation 25–30% (~25 customers)
- Recovered revenue estimate: $18K–$22K
- Cost (20% of recovered): ~$4K → Net: $14K–$18K

[CAMPAIGN_PROPOSAL: At-Risk Win-back Q4 | At Risk | 20 | 92]

### Example 3: Hibernating
**215 Hibernating customers historically spent $31,200 total but haven't ordered in 6+ months.**

| Segment | # Customers | Historical $ | Reactivation Odds |
|---|---|---|---|
| Hibernating | 215 | $31,200 | 5–8% |

Test a 35% reactivation discount on the TOP 50 by historical spend (the rest aren't worth the offer cost). Expected: 3–5 customers return at ~$50 AOV ≈ $150–$250 reactivated. Use this as a learning batch — if response is < 4%, deprecate Hibernating outreach entirely.

[CAMPAIGN_PROPOSAL: Hibernating Test-50 Reactivation | Hibernating | 35 | 50]
"""


def _bool(x) -> bool:
    return bool(x) and str(x).strip() != ""


def build_customer_strategist(
    user_id: str | None = None,
    knowledge: Knowledge | None = None,
    learning: LearningMachine | None = None,
    project_slug: str | None = None,
    actual_user_id: int | None = None,
) -> Agent:
    """Build a Customer Strategist agent scoped to project_slug (or user, or global)."""
    k = knowledge or dash_knowledge
    l = learning or dash_learning

    tools: list = [ReasoningTools()]

    # Engine resolution — same 3-case pattern as data_scientist.py
    if project_slug:
        from db import get_project_readonly_engine
        ro_engine = get_project_readonly_engine(project_slug)
        user_schema = re.sub(r"[^a-z0-9_]", "_", project_slug.lower())[:63]
    elif user_id:
        from db import get_user_readonly_engine
        from db.session import _sanitize_user_id
        ro_engine = get_user_readonly_engine(user_id)
        user_schema = _sanitize_user_id(user_id)
    else:
        from db import get_readonly_engine
        ro_engine = get_readonly_engine()
        user_schema = None

    # ── 7 customer intelligence tools (same wrappers as data_scientist) ──
    try:
        from dash.tools.customer_intelligence import (
            rfm_score as _rfm,
            cohort_curve as _cohort,
        )
        from dash.tools.recommendations import (
            next_best_offer as _nbo,
            item_affinity as _aff,
            popular_products as _pop,
        )
        from dash.tools.clv_churn import (
            clv_score as _clv,
            churn_risk_score as _churn,
        )

        _slug = project_slug

        @_agno_tool(
            name="rfm_score",
            description=(
                "RFM customer segmentation. Scores Recency/Frequency/Monetary into "
                "11 standard segments (Champions, Loyal, At Risk, Hibernating, etc.). "
                "Returns segment counts, top customers per segment, and revenue share. "
                "Args: table (optional), customer_col (optional), date_col (optional), "
                "amount_col (optional)."
            ),
        )
        def _rfm_tool(table: str = "", customer_col: str = "", date_col: str = "", amount_col: str = "") -> str:
            try:
                r = _rfm(_slug, table=table, customer_col=customer_col, date_col=date_col, amount_col=amount_col)
                return _json.dumps(r, default=str)[:6000]
            except Exception as e:
                return _json.dumps({"ok": False, "error": str(e)})

        @_agno_tool(
            name="cohort_curve",
            description=(
                "Cohort retention analysis. Returns retention % matrix per signup "
                "cohort across periods. Args: table (optional), customer_col, date_col, "
                "period ('week'|'month'|'quarter')."
            ),
        )
        def _cohort_tool(table: str = "", customer_col: str = "", date_col: str = "", period: str = "month") -> str:
            try:
                r = _cohort(_slug, table=table, customer_col=customer_col, date_col=date_col, period=period)
                return _json.dumps(r, default=str)[:6000]
            except Exception as e:
                return _json.dumps({"ok": False, "error": str(e)})

        @_agno_tool(
            name="next_best_offer",
            description=(
                "Recommend top-N products for a single customer using collaborative "
                "filtering. Falls back to popularity when customer has no history. "
                "Args: customer_id (required), table, customer_col, sku_col, top_n (default 5)."
            ),
        )
        def _nbo_tool(customer_id: str, table: str = "transactions", customer_col: str = "customer_id", sku_col: str = "sku_id", top_n: int = 5) -> str:
            try:
                r = _nbo(_slug, customer_id=customer_id, table=table, customer_col=customer_col, sku_col=sku_col, top_n=top_n)
                return _json.dumps(r, default=str)[:5000]
            except Exception as e:
                return _json.dumps({"ok": False, "error": str(e)})

        @_agno_tool(
            name="item_affinity",
            description=(
                "Market basket analysis: top-N items co-purchased with target SKU. "
                "Returns lift, support, confidence per pair. "
                "Args: sku_id (required), table, sku_col, basket_col, top_n (default 10)."
            ),
        )
        def _aff_tool(sku_id: str, table: str = "transactions", sku_col: str = "sku_id", basket_col: str = "basket_id", top_n: int = 10) -> str:
            try:
                r = _aff(_slug, sku_id=sku_id, table=table, sku_col=sku_col, basket_col=basket_col, top_n=top_n)
                return _json.dumps(r, default=str)[:5000]
            except Exception as e:
                return _json.dumps({"ok": False, "error": str(e)})

        @_agno_tool(
            name="popular_products",
            description=(
                "Top products by revenue and units in trailing window. "
                "Args: table, sku_col, amount_col, period_days (default 30), top_n (default 10)."
            ),
        )
        def _pop_tool(table: str = "transactions", sku_col: str = "sku_id", amount_col: str = "amount", period_days: int = 30, top_n: int = 10) -> str:
            try:
                r = _pop(_slug, table=table, sku_col=sku_col, amount_col=amount_col, period_days=period_days, top_n=top_n)
                return _json.dumps(r, default=str)[:5000]
            except Exception as e:
                return _json.dumps({"ok": False, "error": str(e)})

        @_agno_tool(
            name="clv_score",
            description=(
                "Customer Lifetime Value. Predicts CLV per customer over horizon_days "
                "using BG/NBD when 'lifetimes' lib is available, else simple proxy. "
                "Args: table, customer_col, date_col, amount_col, horizon_days (default 365)."
            ),
        )
        def _clv_tool(table: str = "transactions", customer_col: str = "customer_id", date_col: str = "ts", amount_col: str = "amount", horizon_days: int = 365) -> str:
            try:
                r = _clv(_slug, table=table, customer_col=customer_col, date_col=date_col, amount_col=amount_col, horizon_days=horizon_days)
                return _json.dumps(r, default=str)[:6000]
            except Exception as e:
                return _json.dumps({"ok": False, "error": str(e)})

        @_agno_tool(
            name="churn_risk_score",
            description=(
                "Score every customer's churn risk: active/cooling/at_risk/churned "
                "based on inter-order gap. Args: table, customer_col, date_col, "
                "dormant_days (default 60)."
            ),
        )
        def _churn_tool(table: str = "transactions", customer_col: str = "customer_id", date_col: str = "ts", dormant_days: int = 60) -> str:
            try:
                r = _churn(_slug, table=table, customer_col=customer_col, date_col=date_col, dormant_days=dormant_days)
                return _json.dumps(r, default=str)[:6000]
            except Exception as e:
                return _json.dumps({"ok": False, "error": str(e)})

        tools.extend([_rfm_tool, _cohort_tool, _nbo_tool, _aff_tool, _pop_tool, _clv_tool, _churn_tool])
    except Exception as _ci_err:
        log.warning("Customer intelligence tools not loaded: %s", _ci_err)

    # ── NEW: propose_campaign tool ──
    @_agno_tool(
        name="propose_campaign",
        description=(
            "Persist a marketing campaign proposal directly into the project's "
            "campaign database. Use this when you've identified a clear segment + "
            "offer combination with quantifiable $ upside. Inserts a draft row into "
            "dash_campaigns and logs a 'created' event in dash_campaign_events. "
            "Args: name (str), target_segment (e.g. 'At Risk', 'Champions', 'rfm:555'), "
            "discount_pct (int, 0-100), offer_description (str), "
            "predicted_audience (int, # of customers expected to receive). "
            "Returns {ok: True, campaign_id: N, audience_size: N} on success."
        ),
    )
    def _propose_campaign_tool(
        name: str,
        target_segment: str,
        discount_pct: int = 0,
        offer_description: str = "",
        predicted_audience: int = 0,
    ) -> str:
        if not project_slug:
            return _json.dumps({"ok": False, "error": "propose_campaign requires a project_slug context"})
        if not name or not target_segment:
            return _json.dumps({"ok": False, "error": "name and target_segment are required"})
        try:
            from sqlalchemy import text as _t
            from db import get_sql_engine
            try:
                discount_pct_i = max(0, min(100, int(discount_pct or 0)))
            except Exception:
                discount_pct_i = 0
            try:
                audience_i = max(0, int(predicted_audience or 0))
            except Exception:
                audience_i = 0

            offer_payload = {
                "discount_pct": discount_pct_i,
                "description": offer_description or "",
                "proposed_by": "customer_strategist",
            }
            target_filter = {"segment": target_segment}

            eng = get_sql_engine()
            with eng.begin() as cn:
                row = cn.execute(
                    _t(
                        """
                        INSERT INTO dash_campaigns
                          (project_slug, name, description, type, status, target_segment,
                           target_filter, audience_size, offer, created_by)
                        VALUES
                          (:slug, :name, :desc, 'manual', 'draft', :seg,
                           CAST(:tf AS jsonb), :asize, CAST(:offer AS jsonb), :cb_by)
                        RETURNING id
                        """
                    ),
                    {
                        "slug": project_slug,
                        "name": name[:200],
                        "desc": (offer_description or "")[:1000],
                        "seg": target_segment[:100],
                        "tf": _json.dumps(target_filter),
                        "asize": audience_i,
                        "offer": _json.dumps(offer_payload),
                        "cb_by": "customer_strategist",
                    },
                ).fetchone()
                cid = int(row[0])

                cn.execute(
                    _t(
                        "INSERT INTO dash_campaign_events "
                        "(campaign_id, event_type, actor, payload) "
                        "VALUES (:cid, 'created', :actor, CAST(:payload AS jsonb))"
                    ),
                    {
                        "cid": cid,
                        "actor": "customer_strategist",
                        "payload": _json.dumps({
                            "source": "agent_proposal",
                            "segment": target_segment,
                            "discount_pct": discount_pct_i,
                            "predicted_audience": audience_i,
                        }),
                    },
                )

            return _json.dumps({
                "ok": True,
                "campaign_id": cid,
                "audience_size": audience_i,
                "status": "draft",
                "message": (
                    f"Campaign '{name}' created as draft (id={cid}) targeting "
                    f"'{target_segment}' with {discount_pct_i}% discount, "
                    f"audience={audience_i}."
                ),
            })
        except Exception as e:
            log.warning("propose_campaign failed: %s", e)
            return _json.dumps({"ok": False, "error": str(e)})

    tools.append(_propose_campaign_tool)

    # ── NEW: mta_summary tool (Tier 4 — Multi-Touch Attribution) ──
    @_agno_tool(
        name="mta_summary",
        description=(
            "Multi-Touch Attribution digest. Returns top channels and top "
            "campaigns by credited revenue under the chosen attribution "
            "model. Call this when the user asks 'which campaigns/channels "
            "drove revenue' or 'what's working in marketing'. "
            "Args: model (linear|time_decay|position|markov, default 'linear'), "
            "days (lookback window, default 30)."
        ),
    )
    def _mta_summary_tool(model: str = "linear", days: int = 30) -> str:
        if not project_slug:
            return _json.dumps({"ok": False, "error": "mta_summary requires a project_slug context"})
        try:
            allowed = {"linear", "time_decay", "position", "markov"}
            m = (model or "linear").strip().lower()
            if m not in allowed:
                m = "linear"
            d = max(1, min(int(days or 30), 365))
            from sqlalchemy import text as _t2
            from db import get_sql_engine
            eng = get_sql_engine()
            ch_sql = """
                SELECT t.channel,
                       COALESCE(SUM(a.credited_revenue), 0) AS rev,
                       COUNT(DISTINCT a.conversion_id) AS conv,
                       SUM(a.credit) AS credit
                FROM dash_attribution_credits a
                JOIN dash_touchpoints t ON t.id = a.touchpoint_id
                JOIN dash_conversions c ON c.id = a.conversion_id
                WHERE a.project_slug = :slug AND a.model = :m
                  AND c.converted_at >= NOW() - (:d || ' days')::interval
                GROUP BY t.channel ORDER BY rev DESC LIMIT 8
            """
            cmp_sql = """
                SELECT COALESCE(camp.name, '(none)') AS name,
                       COALESCE(SUM(a.credited_revenue), 0) AS rev,
                       COUNT(DISTINCT a.conversion_id) AS conv
                FROM dash_attribution_credits a
                JOIN dash_touchpoints t ON t.id = a.touchpoint_id
                JOIN dash_conversions c ON c.id = a.conversion_id
                LEFT JOIN dash_campaigns camp ON camp.id = t.campaign_id
                WHERE a.project_slug = :slug AND a.model = :m
                  AND c.converted_at >= NOW() - (:d || ' days')::interval
                GROUP BY camp.name ORDER BY rev DESC LIMIT 8
            """
            with eng.begin() as cn:
                ch_rows = cn.execute(_t2(ch_sql), {"slug": project_slug, "m": m, "d": str(d)}).fetchall()
                cmp_rows = cn.execute(_t2(cmp_sql), {"slug": project_slug, "m": m, "d": str(d)}).fetchall()
            channels = [
                {"channel": r[0], "credited_revenue": float(r[1] or 0),
                 "conversions": int(r[2] or 0), "credit": float(r[3] or 0)}
                for r in ch_rows
            ]
            campaigns = [
                {"campaign": r[0], "credited_revenue": float(r[1] or 0),
                 "conversions": int(r[2] or 0)}
                for r in cmp_rows
            ]
            total_rev = sum(c["credited_revenue"] for c in channels)
            return _json.dumps({
                "ok": True, "model": m, "days": d,
                "total_credited_revenue": total_rev,
                "top_channels": channels,
                "top_campaigns": campaigns,
            }, default=str)[:6000]
        except Exception as e:
            return _json.dumps({"ok": False, "error": str(e)})

    tools.append(_mta_summary_tool)

    # ── discover_tables (same pattern as data_scientist) ──
    try:
        from db import db_url
        from dash.tools.introspect import create_introspect_schema_tool
        introspect_tool = create_introspect_schema_tool(db_url, engine=ro_engine, user_schema=user_schema)
        introspect_tool.name = "discover_tables"
        introspect_tool.description = (
            "Discover available tables and column names in this project. "
            "Call this FIRST when you don't know the customer/transactions table names."
        )
        tools.append(introspect_tool)
    except (ImportError, AttributeError) as _e:
        log.debug("discover_tables not loaded: %s", _e)

    # SkillRefinery telemetry (same pattern as data_scientist)
    try:
        from dash.tools.skill_refinery import auto_track_list as _sr_track
        _sr_track(tools, agent="customer_strategist", project_slug=project_slug)
    except Exception:
        pass

    return Agent(
        id="customer_strategist",
        name="Customer Strategist",
        role=(
            "Customer-intelligence specialist — RFM segmentation, CLV, churn risk, "
            "next-best-offer, market basket. Auto-suggests campaigns when a clear "
            "segment + offer surface from the data."
        ),
        model=MODEL,
        db=agent_db,
        instructions=CUSTOMER_STRATEGIST_INSTRUCTIONS,
        knowledge=k,
        search_knowledge=True,
        learning=l,
        add_learnings_to_context=True,
        tools=tools,
        add_datetime_to_context=True,
        add_history_to_context=True,
        num_history_runs=3,
        markdown=True,
    )


# Back-compat alias matching the data_scientist `create_*` naming style.
def create_customer_strategist(*args, **kwargs) -> Agent:
    return build_customer_strategist(*args, **kwargs)
