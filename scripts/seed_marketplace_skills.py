"""
Seed Marketplace Skills — 5 verticals × 5 skills = 25 skills
=============================================================

Bootstraps ``dash.dash_skill_marketplace`` with vetted parameterised SQL
recipes so new projects in each vertical have real Day-1 content.

Verticals seeded:
    - pharmacy_network
    - hotel_group
    - bank
    - healthcare
    - supply_chain

Each skill is parameterised SQL using ``${schema}`` placeholder, expanded
to the project schema at install time by the skill installer in
``dash/learning/skill_library.py``.

Idempotent: uses ``ON CONFLICT (name, template_name) DO NOTHING`` against
the unique index ``uq_skill_marketplace_name_template``.

Run inside container:
    docker compose exec -T dash-api python /app/scripts/seed_marketplace_skills.py
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

DEFAULT_JUDGE_SCORE = 4.6
DEFAULT_SUCCESS_COUNT = 20
SOURCE_PROJECT_SLUG = "_seed"


# ═══════════════════════════════════════════════════════════════════════════════
# PHARMACY NETWORK — 5 skills
# ═══════════════════════════════════════════════════════════════════════════════

PHARMACY_SKILLS: list[dict] = [
    {
        "name": "drug_interaction_check",
        "description": (
            "Find active prescriptions containing known interaction drug pairs "
            "(e.g. warfarin + NSAIDs, MAOIs + SSRIs). Joins prescriptions to a "
            "drug_interactions reference table and returns patient_id, both "
            "interacting drugs, clinical severity, and recommended action. "
            "Filters by minimum severity level. Param $1=severity (text: "
            "'minor'|'moderate'|'major'|'contraindicated'), $2=row limit."
        ),
        "sql_template": (
            "SELECT p1.patient_id, "
            "       p1.prescription_id AS rx_a, "
            "       p2.prescription_id AS rx_b, "
            "       p1.drug_name AS drug_a, "
            "       p2.drug_name AS drug_b, "
            "       i.severity, "
            "       i.recommendation, "
            "       p1.prescribed_at "
            "FROM ${schema}.prescriptions p1 "
            "JOIN ${schema}.prescriptions p2 "
            "  ON p1.patient_id = p2.patient_id "
            " AND p1.prescription_id < p2.prescription_id "
            " AND p1.status = 'active' AND p2.status = 'active' "
            "JOIN ${schema}.drug_interactions i "
            "  ON (i.drug_a = p1.drug_name AND i.drug_b = p2.drug_name) "
            "  OR (i.drug_a = p2.drug_name AND i.drug_b = p1.drug_name) "
            "WHERE CASE i.severity "
            "        WHEN 'contraindicated' THEN 4 "
            "        WHEN 'major' THEN 3 "
            "        WHEN 'moderate' THEN 2 "
            "        ELSE 1 END >= "
            "      CASE $1 "
            "        WHEN 'contraindicated' THEN 4 "
            "        WHEN 'major' THEN 3 "
            "        WHEN 'moderate' THEN 2 "
            "        ELSE 1 END "
            "ORDER BY i.severity DESC, p1.prescribed_at DESC "
            "LIMIT $2"
        ),
        "params_schema": {"1": "text", "2": "int"},
        "tags": ["pharmacy", "compliance", "patient_safety", "interactions"],
    },
    {
        "name": "stock_aging_by_site",
        "description": (
            "Compute inventory aging (turnover days) per pharmacy site. "
            "Calculates days_on_hand = current_qty / (sales_velocity_30d / 30) "
            "per (site_id, sku). Flags slow movers (>90 days) and dead stock "
            "(>180 days). Useful for working-capital reviews and write-off "
            "candidates. Param $1=minimum days_on_hand to include (int)."
        ),
        "sql_template": (
            "WITH velocity AS ( "
            "  SELECT s.site_id, s.sku, "
            "         SUM(s.qty_sold)::numeric AS sold_30d "
            "  FROM ${schema}.sales s "
            "  WHERE s.sold_at >= now() - interval '30 days' "
            "  GROUP BY s.site_id, s.sku "
            "), inv AS ( "
            "  SELECT i.site_id, i.sku, i.product_name, "
            "         SUM(i.qty_on_hand) AS qty_on_hand "
            "  FROM ${schema}.inventory i "
            "  GROUP BY i.site_id, i.sku, i.product_name "
            ") "
            "SELECT inv.site_id, inv.sku, inv.product_name, "
            "       inv.qty_on_hand, "
            "       COALESCE(v.sold_30d, 0) AS sold_30d, "
            "       CASE WHEN COALESCE(v.sold_30d, 0) > 0 "
            "            THEN ROUND((inv.qty_on_hand * 30.0 / v.sold_30d)::numeric, 1) "
            "            ELSE 9999 END AS days_on_hand, "
            "       CASE WHEN COALESCE(v.sold_30d, 0) = 0 THEN 'dead_stock' "
            "            WHEN (inv.qty_on_hand * 30.0 / v.sold_30d) > 180 THEN 'dead_stock' "
            "            WHEN (inv.qty_on_hand * 30.0 / v.sold_30d) > 90 THEN 'slow_mover' "
            "            ELSE 'active' END AS aging_status "
            "FROM inv "
            "LEFT JOIN velocity v ON v.site_id = inv.site_id AND v.sku = inv.sku "
            "WHERE COALESCE((inv.qty_on_hand * 30.0 / NULLIF(v.sold_30d, 0)), 9999) >= $1 "
            "ORDER BY days_on_hand DESC"
        ),
        "params_schema": {"1": "int"},
        "tags": ["pharmacy", "inventory", "aging", "working_capital"],
    },
    {
        "name": "controlled_substance_audit",
        "description": (
            "DEA-style audit for Schedule II/III controlled substance "
            "dispensations over the last 30 days. Returns dispensation events "
            "joined to dispensing pharmacist and prescriber, ranked by "
            "quantity. Flags suspicious volumes (>3 sigma above pharmacist's "
            "30d average). Param $1=schedule class (text: 'II'|'III'|'IV')."
        ),
        "sql_template": (
            "WITH disp AS ( "
            "  SELECT d.dispensation_id, d.patient_id, d.drug_name, d.schedule_class, "
            "         d.qty_dispensed, d.pharmacist_id, d.prescriber_id, d.dispensed_at "
            "  FROM ${schema}.dispensations d "
            "  WHERE d.dispensed_at >= now() - interval '30 days' "
            "    AND d.schedule_class = $1 "
            "), pharm_stats AS ( "
            "  SELECT pharmacist_id, "
            "         AVG(qty_dispensed) AS avg_qty, "
            "         STDDEV(qty_dispensed) AS std_qty "
            "  FROM disp "
            "  GROUP BY pharmacist_id "
            ") "
            "SELECT d.dispensation_id, d.dispensed_at, d.patient_id, d.drug_name, "
            "       d.schedule_class, d.qty_dispensed, "
            "       d.pharmacist_id, d.prescriber_id, "
            "       ROUND(ps.avg_qty::numeric, 2) AS pharmacist_avg_qty, "
            "       CASE WHEN ps.std_qty > 0 "
            "              AND d.qty_dispensed > ps.avg_qty + 3 * ps.std_qty "
            "            THEN 'suspicious' ELSE 'normal' END AS flag "
            "FROM disp d "
            "JOIN pharm_stats ps ON ps.pharmacist_id = d.pharmacist_id "
            "ORDER BY d.qty_dispensed DESC, d.dispensed_at DESC"
        ),
        "params_schema": {"1": "text"},
        "tags": ["pharmacy", "compliance", "dea", "controlled_substance", "audit"],
    },
    {
        "name": "low_stock_alerts",
        "description": (
            "Identify SKUs below the per-site reorder threshold. Returns site, "
            "SKU, product name, current qty_on_hand, reorder_point, days_to_stockout "
            "(based on 14d sales velocity), and suggested reorder qty (max_stock "
            "minus current). Triages by urgency. Param $1=urgency threshold "
            "in days_to_stockout (int, e.g. 7 for week-out alerts)."
        ),
        "sql_template": (
            "WITH velocity AS ( "
            "  SELECT s.site_id, s.sku, "
            "         SUM(s.qty_sold)::numeric / 14.0 AS daily_velocity "
            "  FROM ${schema}.sales s "
            "  WHERE s.sold_at >= now() - interval '14 days' "
            "  GROUP BY s.site_id, s.sku "
            ") "
            "SELECT i.site_id, i.sku, i.product_name, "
            "       i.qty_on_hand, i.reorder_point, i.max_stock, "
            "       COALESCE(v.daily_velocity, 0) AS daily_velocity, "
            "       CASE WHEN COALESCE(v.daily_velocity, 0) > 0 "
            "            THEN ROUND((i.qty_on_hand / v.daily_velocity)::numeric, 1) "
            "            ELSE 9999 END AS days_to_stockout, "
            "       GREATEST(i.max_stock - i.qty_on_hand, 0) AS suggested_reorder_qty "
            "FROM ${schema}.inventory i "
            "LEFT JOIN velocity v ON v.site_id = i.site_id AND v.sku = i.sku "
            "WHERE i.qty_on_hand < i.reorder_point "
            "  AND COALESCE((i.qty_on_hand / NULLIF(v.daily_velocity, 0)), 9999) <= $1 "
            "ORDER BY days_to_stockout ASC, i.site_id"
        ),
        "params_schema": {"1": "int"},
        "tags": ["pharmacy", "inventory", "reorder", "alerts"],
    },
    {
        "name": "expiry_window_report",
        "description": (
            "List inventory items expiring within the next N days across all "
            "sites, with at-risk inventory value (qty × unit_cost). Critical "
            "for FEFO (first-expire-first-out) rotation and write-off "
            "forecasting. Param $1=days ahead window (int, e.g. 90)."
        ),
        "sql_template": (
            "SELECT i.site_id, i.sku, i.product_name, i.lot_number, "
            "       i.qty_on_hand, i.expiry_date, "
            "       (i.expiry_date - CURRENT_DATE) AS days_to_expiry, "
            "       i.unit_cost, "
            "       ROUND((i.qty_on_hand * i.unit_cost)::numeric, 2) AS at_risk_value, "
            "       CASE WHEN i.expiry_date < CURRENT_DATE THEN 'expired' "
            "            WHEN i.expiry_date <= CURRENT_DATE + interval '30 days' THEN 'critical' "
            "            WHEN i.expiry_date <= CURRENT_DATE + interval '60 days' THEN 'urgent' "
            "            ELSE 'monitor' END AS urgency "
            "FROM ${schema}.inventory i "
            "WHERE i.expiry_date IS NOT NULL "
            "  AND i.qty_on_hand > 0 "
            "  AND i.expiry_date <= CURRENT_DATE + ($1 || ' days')::interval "
            "ORDER BY i.expiry_date ASC, at_risk_value DESC"
        ),
        "params_schema": {"1": "int"},
        "tags": ["pharmacy", "inventory", "expiry", "fefo", "compliance"],
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# HOTEL GROUP — 5 skills
# ═══════════════════════════════════════════════════════════════════════════════

HOTEL_SKILLS: list[dict] = [
    {
        "name": "daily_adr_calc",
        "description": (
            "Compute Average Daily Rate (ADR) per property over a date range. "
            "ADR = total_room_revenue / rooms_sold. Excludes complimentary "
            "stays. Standard hotel KPI for revenue management. "
            "Param $1=start_date (date), $2=end_date (date)."
        ),
        "sql_template": (
            "SELECT r.property_id, p.property_name, "
            "       DATE(r.stay_date) AS stay_date, "
            "       COUNT(*) AS rooms_sold, "
            "       SUM(r.room_revenue)::numeric AS room_revenue, "
            "       ROUND((SUM(r.room_revenue) / NULLIF(COUNT(*), 0))::numeric, 2) AS adr "
            "FROM ${schema}.reservations r "
            "JOIN ${schema}.properties p ON p.property_id = r.property_id "
            "WHERE r.stay_date BETWEEN $1 AND $2 "
            "  AND r.status IN ('checked_in', 'checked_out') "
            "  AND COALESCE(r.is_complimentary, FALSE) = FALSE "
            "GROUP BY r.property_id, p.property_name, DATE(r.stay_date) "
            "ORDER BY stay_date DESC, adr DESC"
        ),
        "params_schema": {"1": "date", "2": "date"},
        "tags": ["hotel", "kpi", "adr", "revenue_management"],
    },
    {
        "name": "revpar_trend",
        "description": (
            "12-month trend of Revenue Per Available Room (RevPAR) per property. "
            "RevPAR = room_revenue / rooms_available = ADR × occupancy. Compares "
            "to prior year same month (YoY delta). Critical for board reporting. "
            "Param $1=property_id (text), $2=months back (int, e.g. 12)."
        ),
        "sql_template": (
            "WITH monthly AS ( "
            "  SELECT r.property_id, "
            "         DATE_TRUNC('month', r.stay_date)::date AS month, "
            "         SUM(r.room_revenue)::numeric AS room_revenue, "
            "         SUM(p.total_rooms) AS rooms_available, "
            "         COUNT(*) FILTER (WHERE r.status IN ('checked_in','checked_out')) AS rooms_sold "
            "  FROM ${schema}.reservations r "
            "  JOIN ${schema}.properties p ON p.property_id = r.property_id "
            "  WHERE r.property_id = $1 "
            "    AND r.stay_date >= (CURRENT_DATE - ($2 || ' months')::interval) "
            "  GROUP BY r.property_id, DATE_TRUNC('month', r.stay_date) "
            ") "
            "SELECT month, room_revenue, rooms_available, rooms_sold, "
            "       ROUND((room_revenue / NULLIF(rooms_available, 0))::numeric, 2) AS revpar, "
            "       ROUND((room_revenue / NULLIF(rooms_sold, 0))::numeric, 2) AS adr, "
            "       ROUND((rooms_sold * 100.0 / NULLIF(rooms_available, 0))::numeric, 2) AS occupancy_pct, "
            "       LAG(room_revenue / NULLIF(rooms_available, 0)) OVER (ORDER BY month) AS revpar_prior, "
            "       ROUND(((room_revenue / NULLIF(rooms_available, 0)) - "
            "              LAG(room_revenue / NULLIF(rooms_available, 0)) OVER (ORDER BY month))::numeric, 2) AS revpar_delta "
            "FROM monthly "
            "ORDER BY month"
        ),
        "params_schema": {"1": "text", "2": "int"},
        "tags": ["hotel", "kpi", "revpar", "trend", "yoy"],
    },
    {
        "name": "occupancy_by_segment",
        "description": (
            "Break down occupancy and revenue mix by market segment (corporate, "
            "leisure, OTA, group, government). Returns rooms_sold, revenue_share, "
            "and segment-level ADR for the given period. Drives channel-mix "
            "decisions. Param $1=start_date (date), $2=end_date (date)."
        ),
        "sql_template": (
            "WITH seg AS ( "
            "  SELECT r.market_segment, "
            "         COUNT(*) AS rooms_sold, "
            "         SUM(r.room_revenue)::numeric AS revenue "
            "  FROM ${schema}.reservations r "
            "  WHERE r.stay_date BETWEEN $1 AND $2 "
            "    AND r.status IN ('checked_in', 'checked_out') "
            "  GROUP BY r.market_segment "
            "), total AS ( "
            "  SELECT SUM(rooms_sold)::numeric AS total_rooms, "
            "         SUM(revenue)::numeric AS total_revenue "
            "  FROM seg "
            ") "
            "SELECT s.market_segment, s.rooms_sold, "
            "       ROUND((s.rooms_sold * 100.0 / t.total_rooms)::numeric, 2) AS room_share_pct, "
            "       s.revenue, "
            "       ROUND((s.revenue * 100.0 / t.total_revenue)::numeric, 2) AS revenue_share_pct, "
            "       ROUND((s.revenue / NULLIF(s.rooms_sold, 0))::numeric, 2) AS segment_adr "
            "FROM seg s CROSS JOIN total t "
            "ORDER BY s.revenue DESC"
        ),
        "params_schema": {"1": "date", "2": "date"},
        "tags": ["hotel", "segment", "mix", "channel"],
    },
    {
        "name": "ancillary_revenue_breakdown",
        "description": (
            "Breakdown of non-room (ancillary) revenue: F&B, spa, parking, "
            "minibar, laundry, etc., as % of total revenue per property. "
            "Highlights upsell performance. Param $1=property_id (text), "
            "$2=start_date (date), $3=end_date (date)."
        ),
        "sql_template": (
            "WITH rev AS ( "
            "  SELECT a.revenue_category, "
            "         SUM(a.amount)::numeric AS category_revenue "
            "  FROM ${schema}.ancillary_revenue a "
            "  WHERE a.property_id = $1 "
            "    AND a.charge_date BETWEEN $2 AND $3 "
            "  GROUP BY a.revenue_category "
            "  UNION ALL "
            "  SELECT 'room' AS revenue_category, "
            "         SUM(r.room_revenue)::numeric AS category_revenue "
            "  FROM ${schema}.reservations r "
            "  WHERE r.property_id = $1 "
            "    AND r.stay_date BETWEEN $2 AND $3 "
            "    AND r.status IN ('checked_in', 'checked_out') "
            "), total AS ( "
            "  SELECT SUM(category_revenue) AS grand_total FROM rev "
            ") "
            "SELECT rev.revenue_category, rev.category_revenue, "
            "       ROUND((rev.category_revenue * 100.0 / NULLIF(t.grand_total, 0))::numeric, 2) AS pct_of_total "
            "FROM rev CROSS JOIN total t "
            "ORDER BY rev.category_revenue DESC"
        ),
        "params_schema": {"1": "text", "2": "date", "3": "date"},
        "tags": ["hotel", "ancillary", "fnb", "spa", "revenue_mix"],
    },
    {
        "name": "cancellation_rate_alert",
        "description": (
            "Flags properties/segments with cancellation rate above a threshold "
            "(default 15%). Returns per-property, per-segment cancellation rate, "
            "bookings count, and lost revenue estimate. Critical for revenue "
            "leakage monitoring. Param $1=threshold pct (float, e.g. 15.0), "
            "$2=lookback days (int)."
        ),
        "sql_template": (
            "WITH bookings AS ( "
            "  SELECT r.property_id, r.market_segment, "
            "         COUNT(*) AS total_bookings, "
            "         COUNT(*) FILTER (WHERE r.status = 'cancelled') AS cancelled, "
            "         SUM(r.room_revenue) FILTER (WHERE r.status = 'cancelled')::numeric AS lost_revenue "
            "  FROM ${schema}.reservations r "
            "  WHERE r.booked_at >= now() - ($2 || ' days')::interval "
            "  GROUP BY r.property_id, r.market_segment "
            ") "
            "SELECT b.property_id, b.market_segment, "
            "       b.total_bookings, b.cancelled, "
            "       ROUND((b.cancelled * 100.0 / NULLIF(b.total_bookings, 0))::numeric, 2) AS cancel_rate_pct, "
            "       COALESCE(b.lost_revenue, 0) AS lost_revenue "
            "FROM bookings b "
            "WHERE (b.cancelled * 100.0 / NULLIF(b.total_bookings, 0)) >= $1 "
            "ORDER BY cancel_rate_pct DESC, lost_revenue DESC"
        ),
        "params_schema": {"1": "float", "2": "int"},
        "tags": ["hotel", "cancellation", "alert", "revenue_leakage"],
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# BANK — 5 skills
# ═══════════════════════════════════════════════════════════════════════════════

BANK_SKILLS: list[dict] = [
    {
        "name": "npl_ratio_by_branch",
        "description": (
            "Non-Performing Loan (NPL) ratio per branch — loans >90 days "
            "past due as % of total outstanding principal. Core credit-risk "
            "KPI. Param $1=DPD threshold (int, e.g. 90), $2=minimum portfolio "
            "size to include (numeric)."
        ),
        "sql_template": (
            "WITH stats AS ( "
            "  SELECT l.branch_id, "
            "         SUM(l.outstanding_principal)::numeric AS total_outstanding, "
            "         SUM(l.outstanding_principal) FILTER (WHERE l.days_past_due > $1)::numeric AS npl_outstanding, "
            "         COUNT(*) FILTER (WHERE l.days_past_due > $1) AS npl_count, "
            "         COUNT(*) AS total_loans "
            "  FROM ${schema}.loans l "
            "  WHERE l.status NOT IN ('closed', 'written_off') "
            "  GROUP BY l.branch_id "
            ") "
            "SELECT s.branch_id, s.total_loans, s.npl_count, "
            "       s.total_outstanding, COALESCE(s.npl_outstanding, 0) AS npl_outstanding, "
            "       ROUND((COALESCE(s.npl_outstanding, 0) * 100.0 / NULLIF(s.total_outstanding, 0))::numeric, 2) AS npl_ratio_pct "
            "FROM stats s "
            "WHERE s.total_outstanding >= $2 "
            "ORDER BY npl_ratio_pct DESC"
        ),
        "params_schema": {"1": "int", "2": "float"},
        "tags": ["bank", "credit_risk", "npl", "branch", "compliance"],
    },
    {
        "name": "fraud_alert_volume",
        "description": (
            "Daily fraud-flag volume trend over the last N days. Returns "
            "alerts/day broken by alert type, with day-over-day delta and "
            "7-day rolling average. Identifies emerging fraud waves. "
            "Param $1=lookback days (int, e.g. 30)."
        ),
        "sql_template": (
            "WITH daily AS ( "
            "  SELECT DATE(f.flagged_at) AS day, "
            "         f.alert_type, "
            "         COUNT(*) AS alert_count "
            "  FROM ${schema}.fraud_alerts f "
            "  WHERE f.flagged_at >= now() - ($1 || ' days')::interval "
            "  GROUP BY DATE(f.flagged_at), f.alert_type "
            ") "
            "SELECT d.day, d.alert_type, d.alert_count, "
            "       LAG(d.alert_count) OVER (PARTITION BY d.alert_type ORDER BY d.day) AS prior_day, "
            "       d.alert_count - LAG(d.alert_count) OVER (PARTITION BY d.alert_type ORDER BY d.day) AS day_delta, "
            "       ROUND(AVG(d.alert_count) OVER ( "
            "         PARTITION BY d.alert_type ORDER BY d.day "
            "         ROWS BETWEEN 6 PRECEDING AND CURRENT ROW "
            "       )::numeric, 2) AS rolling_7d_avg "
            "FROM daily d "
            "ORDER BY d.day DESC, d.alert_count DESC"
        ),
        "params_schema": {"1": "int"},
        "tags": ["bank", "fraud", "alert", "trend"],
    },
    {
        "name": "top_deposit_growth_accounts",
        "description": (
            "Top N accounts ranked by absolute deposit growth over the last "
            "30 days. Compares current balance to balance 30 days ago. "
            "Useful for relationship-manager outreach and retention. "
            "Param $1=number of accounts (int, e.g. 20)."
        ),
        "sql_template": (
            "WITH bal_now AS ( "
            "  SELECT account_id, balance AS current_balance "
            "  FROM ${schema}.account_balances "
            "  WHERE snapshot_date = ( "
            "    SELECT MAX(snapshot_date) FROM ${schema}.account_balances "
            "  ) "
            "), bal_then AS ( "
            "  SELECT DISTINCT ON (account_id) account_id, balance AS prior_balance "
            "  FROM ${schema}.account_balances "
            "  WHERE snapshot_date <= CURRENT_DATE - interval '30 days' "
            "  ORDER BY account_id, snapshot_date DESC "
            ") "
            "SELECT a.account_id, a.customer_name, a.account_type, a.branch_id, "
            "       n.current_balance, "
            "       COALESCE(t.prior_balance, 0) AS prior_balance, "
            "       (n.current_balance - COALESCE(t.prior_balance, 0)) AS growth_amount, "
            "       ROUND(((n.current_balance - COALESCE(t.prior_balance, 0)) * 100.0 / "
            "              NULLIF(t.prior_balance, 0))::numeric, 2) AS growth_pct "
            "FROM bal_now n "
            "JOIN ${schema}.accounts a ON a.account_id = n.account_id "
            "LEFT JOIN bal_then t ON t.account_id = n.account_id "
            "WHERE n.current_balance > COALESCE(t.prior_balance, 0) "
            "ORDER BY growth_amount DESC "
            "LIMIT $1"
        ),
        "params_schema": {"1": "int"},
        "tags": ["bank", "deposit", "growth", "relationship_management"],
    },
    {
        "name": "loan_concentration_check",
        "description": (
            "Flags single-borrower exposures exceeding a regulatory limit "
            "(typically 25% of bank capital). Returns borrowers, total "
            "exposure across all loans, and concentration ratio vs limit. "
            "Param $1=exposure limit amount (numeric)."
        ),
        "sql_template": (
            "WITH borrower_exp AS ( "
            "  SELECT l.borrower_id, "
            "         COUNT(*) AS loan_count, "
            "         SUM(l.outstanding_principal)::numeric AS total_exposure "
            "  FROM ${schema}.loans l "
            "  WHERE l.status = 'active' "
            "  GROUP BY l.borrower_id "
            ") "
            "SELECT be.borrower_id, b.borrower_name, b.industry, "
            "       be.loan_count, be.total_exposure, "
            "       $1::numeric AS exposure_limit, "
            "       ROUND((be.total_exposure / $1)::numeric * 100, 2) AS pct_of_limit, "
            "       CASE WHEN be.total_exposure > $1 THEN 'breach' "
            "            WHEN be.total_exposure > 0.8 * $1 THEN 'warning' "
            "            ELSE 'ok' END AS status "
            "FROM borrower_exp be "
            "JOIN ${schema}.borrowers b ON b.borrower_id = be.borrower_id "
            "WHERE be.total_exposure > 0.5 * $1 "
            "ORDER BY be.total_exposure DESC"
        ),
        "params_schema": {"1": "float"},
        "tags": ["bank", "credit_risk", "concentration", "regulatory"],
    },
    {
        "name": "transaction_anomaly_screen",
        "description": (
            "Flag today's transactions whose amount Z-score exceeds 3 against "
            "the customer's 90-day baseline (mean + stddev). Identifies "
            "potential account takeover, fraud, or mistaken-amount keying. "
            "Param $1=Z-score threshold (float, e.g. 3.0)."
        ),
        "sql_template": (
            "WITH baseline AS ( "
            "  SELECT t.account_id, "
            "         AVG(t.amount) AS mean_amt, "
            "         STDDEV(t.amount) AS std_amt "
            "  FROM ${schema}.transactions t "
            "  WHERE t.txn_date >= CURRENT_DATE - interval '90 days' "
            "    AND t.txn_date < CURRENT_DATE "
            "  GROUP BY t.account_id "
            "  HAVING COUNT(*) >= 5 AND STDDEV(t.amount) > 0 "
            ") "
            "SELECT t.txn_id, t.account_id, t.amount, t.txn_type, t.txn_at, "
            "       ROUND(b.mean_amt::numeric, 2) AS baseline_mean, "
            "       ROUND(b.std_amt::numeric, 2) AS baseline_std, "
            "       ROUND(((t.amount - b.mean_amt) / NULLIF(b.std_amt, 0))::numeric, 2) AS z_score "
            "FROM ${schema}.transactions t "
            "JOIN baseline b ON b.account_id = t.account_id "
            "WHERE DATE(t.txn_at) = CURRENT_DATE "
            "  AND ABS((t.amount - b.mean_amt) / NULLIF(b.std_amt, 0)) > $1 "
            "ORDER BY ABS((t.amount - b.mean_amt) / NULLIF(b.std_amt, 0)) DESC"
        ),
        "params_schema": {"1": "float"},
        "tags": ["bank", "fraud", "anomaly", "z_score", "transaction"],
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTHCARE — 5 skills
# ═══════════════════════════════════════════════════════════════════════════════

HEALTHCARE_SKILLS: list[dict] = [
    {
        "name": "los_by_diagnosis",
        "description": (
            "Average length-of-stay (LOS) per DRG / primary diagnosis code, "
            "with median, p90, and case count. Filters to encounters discharged "
            "in last N days. Compares against hospital-wide LOS benchmark. "
            "Param $1=lookback days (int, e.g. 90)."
        ),
        "sql_template": (
            "WITH discharged AS ( "
            "  SELECT e.encounter_id, e.drg_code, e.primary_diagnosis, "
            "         EXTRACT(EPOCH FROM (e.discharged_at - e.admitted_at)) / 86400.0 AS los_days "
            "  FROM ${schema}.encounters e "
            "  WHERE e.discharged_at >= now() - ($1 || ' days')::interval "
            "    AND e.discharged_at IS NOT NULL "
            "    AND e.admitted_at IS NOT NULL "
            ") "
            "SELECT drg_code, primary_diagnosis, "
            "       COUNT(*) AS case_count, "
            "       ROUND(AVG(los_days)::numeric, 2) AS avg_los_days, "
            "       ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY los_days))::numeric, 2) AS median_los, "
            "       ROUND((PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY los_days))::numeric, 2) AS p90_los, "
            "       ROUND(MIN(los_days)::numeric, 2) AS min_los, "
            "       ROUND(MAX(los_days)::numeric, 2) AS max_los "
            "FROM discharged "
            "GROUP BY drg_code, primary_diagnosis "
            "HAVING COUNT(*) >= 3 "
            "ORDER BY case_count DESC, avg_los_days DESC"
        ),
        "params_schema": {"1": "int"},
        "tags": ["healthcare", "los", "drg", "operations"],
    },
    {
        "name": "readmission_30d_rate",
        "description": (
            "30-day readmission rate per unit / diagnosis. CMS-style quality "
            "metric: % of discharges followed by an unplanned readmission "
            "within 30 days. Stratified by primary_diagnosis. Param $1="
            "lookback days for index discharges (int, e.g. 180)."
        ),
        "sql_template": (
            "WITH index_discharges AS ( "
            "  SELECT e.encounter_id, e.patient_id, e.primary_diagnosis, "
            "         e.discharged_at, e.unit "
            "  FROM ${schema}.encounters e "
            "  WHERE e.discharged_at >= now() - ($1 || ' days')::interval "
            "    AND e.discharged_at <= now() - interval '30 days' "
            "    AND e.discharged_at IS NOT NULL "
            "), readmits AS ( "
            "  SELECT idx.encounter_id, idx.patient_id, idx.primary_diagnosis, idx.unit, "
            "         EXISTS ( "
            "           SELECT 1 FROM ${schema}.encounters re "
            "           WHERE re.patient_id = idx.patient_id "
            "             AND re.admitted_at > idx.discharged_at "
            "             AND re.admitted_at <= idx.discharged_at + interval '30 days' "
            "             AND COALESCE(re.admission_type, '') != 'planned' "
            "         ) AS readmitted "
            "  FROM index_discharges idx "
            ") "
            "SELECT unit, primary_diagnosis, "
            "       COUNT(*) AS index_discharges, "
            "       COUNT(*) FILTER (WHERE readmitted) AS readmissions, "
            "       ROUND((COUNT(*) FILTER (WHERE readmitted) * 100.0 / COUNT(*))::numeric, 2) AS readmit_rate_pct "
            "FROM readmits "
            "GROUP BY unit, primary_diagnosis "
            "HAVING COUNT(*) >= 5 "
            "ORDER BY readmit_rate_pct DESC"
        ),
        "params_schema": {"1": "int"},
        "tags": ["healthcare", "readmission", "quality", "cms"],
    },
    {
        "name": "provider_productivity",
        "description": (
            "Encounters per provider per shift over the last N days. Returns "
            "provider name, role, total encounters, avg/shift, and total "
            "hours worked. Identifies overworked and under-utilised providers. "
            "Param $1=lookback days (int, e.g. 30)."
        ),
        "sql_template": (
            "WITH shifts AS ( "
            "  SELECT s.provider_id, COUNT(*) AS shift_count, "
            "         SUM(EXTRACT(EPOCH FROM (s.shift_end - s.shift_start)) / 3600.0) AS total_hours "
            "  FROM ${schema}.provider_shifts s "
            "  WHERE s.shift_start >= now() - ($1 || ' days')::interval "
            "  GROUP BY s.provider_id "
            "), enc AS ( "
            "  SELECT e.provider_id, COUNT(*) AS encounter_count "
            "  FROM ${schema}.encounters e "
            "  WHERE e.admitted_at >= now() - ($1 || ' days')::interval "
            "  GROUP BY e.provider_id "
            ") "
            "SELECT p.provider_id, p.provider_name, p.role, p.specialty, "
            "       COALESCE(s.shift_count, 0) AS shifts, "
            "       ROUND(COALESCE(s.total_hours, 0)::numeric, 1) AS total_hours, "
            "       COALESCE(e.encounter_count, 0) AS total_encounters, "
            "       ROUND((COALESCE(e.encounter_count, 0)::numeric / NULLIF(s.shift_count, 0)), 2) AS enc_per_shift, "
            "       ROUND((COALESCE(e.encounter_count, 0)::numeric / NULLIF(s.total_hours, 0)), 2) AS enc_per_hour "
            "FROM ${schema}.providers p "
            "LEFT JOIN shifts s ON s.provider_id = p.provider_id "
            "LEFT JOIN enc e ON e.provider_id = p.provider_id "
            "WHERE COALESCE(s.shift_count, 0) > 0 "
            "ORDER BY enc_per_shift DESC"
        ),
        "params_schema": {"1": "int"},
        "tags": ["healthcare", "productivity", "provider", "operations"],
    },
    {
        "name": "lab_turnaround_outliers",
        "description": (
            "Lab orders with turnaround time (TAT) exceeding 24 hours from "
            "specimen collection to result reported. Returns order details, "
            "TAT in hours, and ordering provider. Identifies bottlenecks. "
            "Param $1=TAT threshold in hours (int, e.g. 24)."
        ),
        "sql_template": (
            "SELECT l.lab_order_id, l.patient_id, l.test_code, l.test_name, "
            "       l.collected_at, l.resulted_at, "
            "       ROUND((EXTRACT(EPOCH FROM (l.resulted_at - l.collected_at)) / 3600.0)::numeric, 2) AS tat_hours, "
            "       l.ordering_provider_id, l.lab_section, l.priority "
            "FROM ${schema}.lab_orders l "
            "WHERE l.collected_at IS NOT NULL "
            "  AND l.resulted_at IS NOT NULL "
            "  AND l.resulted_at >= now() - interval '30 days' "
            "  AND EXTRACT(EPOCH FROM (l.resulted_at - l.collected_at)) / 3600.0 > $1 "
            "ORDER BY tat_hours DESC"
        ),
        "params_schema": {"1": "int"},
        "tags": ["healthcare", "lab", "tat", "quality", "outlier"],
    },
    {
        "name": "bed_occupancy_by_unit",
        "description": (
            "Current bed occupancy % per ward/unit. Returns total beds, "
            "occupied beds, occupancy_pct, and average LOS for current "
            "inpatients. Critical for capacity management. No params (live "
            "snapshot)."
        ),
        "sql_template": (
            "WITH occ AS ( "
            "  SELECT u.unit_id, u.unit_name, u.total_beds, "
            "         COUNT(e.encounter_id) FILTER (WHERE e.discharged_at IS NULL) AS occupied_beds, "
            "         AVG(EXTRACT(EPOCH FROM (now() - e.admitted_at)) / 86400.0) "
            "           FILTER (WHERE e.discharged_at IS NULL) AS avg_current_los "
            "  FROM ${schema}.units u "
            "  LEFT JOIN ${schema}.encounters e ON e.unit = u.unit_name "
            "  GROUP BY u.unit_id, u.unit_name, u.total_beds "
            ") "
            "SELECT unit_id, unit_name, total_beds, occupied_beds, "
            "       (total_beds - occupied_beds) AS available_beds, "
            "       ROUND((occupied_beds * 100.0 / NULLIF(total_beds, 0))::numeric, 2) AS occupancy_pct, "
            "       ROUND(avg_current_los::numeric, 2) AS avg_current_los_days, "
            "       CASE WHEN (occupied_beds * 100.0 / NULLIF(total_beds, 0)) >= 95 THEN 'critical' "
            "            WHEN (occupied_beds * 100.0 / NULLIF(total_beds, 0)) >= 85 THEN 'high' "
            "            WHEN (occupied_beds * 100.0 / NULLIF(total_beds, 0)) >= 70 THEN 'normal' "
            "            ELSE 'low' END AS status "
            "FROM occ "
            "ORDER BY occupancy_pct DESC"
        ),
        "params_schema": {},
        "tags": ["healthcare", "capacity", "occupancy", "bed_management"],
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# SUPPLY CHAIN — 5 skills
# ═══════════════════════════════════════════════════════════════════════════════

SUPPLY_CHAIN_SKILLS: list[dict] = [
    {
        "name": "otif_by_supplier",
        "description": (
            "On-Time-In-Full (OTIF) performance per supplier over the last "
            "90 days. OTIF = orders delivered on-or-before promised date AND "
            "with full requested quantity. Industry-standard supplier KPI. "
            "Param $1=lookback days (int, e.g. 90)."
        ),
        "sql_template": (
            "WITH orders AS ( "
            "  SELECT po.supplier_id, po.po_id, "
            "         (po.delivered_at <= po.promised_date) AS on_time, "
            "         (po.qty_delivered >= po.qty_ordered) AS in_full "
            "  FROM ${schema}.purchase_orders po "
            "  WHERE po.delivered_at IS NOT NULL "
            "    AND po.delivered_at >= now() - ($1 || ' days')::interval "
            ") "
            "SELECT s.supplier_id, s.supplier_name, "
            "       COUNT(*) AS total_orders, "
            "       COUNT(*) FILTER (WHERE on_time) AS on_time_orders, "
            "       COUNT(*) FILTER (WHERE in_full) AS in_full_orders, "
            "       COUNT(*) FILTER (WHERE on_time AND in_full) AS otif_orders, "
            "       ROUND((COUNT(*) FILTER (WHERE on_time) * 100.0 / COUNT(*))::numeric, 2) AS on_time_pct, "
            "       ROUND((COUNT(*) FILTER (WHERE in_full) * 100.0 / COUNT(*))::numeric, 2) AS in_full_pct, "
            "       ROUND((COUNT(*) FILTER (WHERE on_time AND in_full) * 100.0 / COUNT(*))::numeric, 2) AS otif_pct "
            "FROM orders o "
            "JOIN ${schema}.suppliers s ON s.supplier_id = o.supplier_id "
            "GROUP BY s.supplier_id, s.supplier_name "
            "HAVING COUNT(*) >= 3 "
            "ORDER BY otif_pct ASC"
        ),
        "params_schema": {"1": "int"},
        "tags": ["supply_chain", "otif", "supplier", "kpi"],
    },
    {
        "name": "defect_rate_trend",
        "description": (
            "Monthly defect rate (% of units defective) per supplier over the "
            "last 12 months. Tracks quality trend with month-over-month delta. "
            "Critical for supplier scorecards. Param $1=months back (int)."
        ),
        "sql_template": (
            "WITH monthly AS ( "
            "  SELECT DATE_TRUNC('month', q.inspected_at)::date AS month, "
            "         q.supplier_id, "
            "         SUM(q.units_inspected) AS units_inspected, "
            "         SUM(q.units_defective) AS units_defective "
            "  FROM ${schema}.quality_inspections q "
            "  WHERE q.inspected_at >= (CURRENT_DATE - ($1 || ' months')::interval) "
            "  GROUP BY DATE_TRUNC('month', q.inspected_at), q.supplier_id "
            ") "
            "SELECT m.month, s.supplier_name, "
            "       m.units_inspected, m.units_defective, "
            "       ROUND((m.units_defective * 100.0 / NULLIF(m.units_inspected, 0))::numeric, 3) AS defect_rate_pct, "
            "       LAG(m.units_defective * 100.0 / NULLIF(m.units_inspected, 0)) "
            "         OVER (PARTITION BY m.supplier_id ORDER BY m.month) AS prior_month_pct, "
            "       ROUND(((m.units_defective * 100.0 / NULLIF(m.units_inspected, 0)) - "
            "              LAG(m.units_defective * 100.0 / NULLIF(m.units_inspected, 0)) "
            "                OVER (PARTITION BY m.supplier_id ORDER BY m.month))::numeric, 3) AS mom_delta_pct "
            "FROM monthly m "
            "JOIN ${schema}.suppliers s ON s.supplier_id = m.supplier_id "
            "ORDER BY m.month DESC, defect_rate_pct DESC"
        ),
        "params_schema": {"1": "int"},
        "tags": ["supply_chain", "quality", "defect", "supplier", "trend"],
    },
    {
        "name": "supplier_concentration_risk",
        "description": (
            "Top 5 suppliers as % of total procurement spend last 365 days. "
            "Concentration risk indicator — single-supplier dependence above "
            "20% is typically flagged. Returns rank, supplier, total_spend, "
            "and pct_of_total. No params."
        ),
        "sql_template": (
            "WITH supplier_spend AS ( "
            "  SELECT po.supplier_id, "
            "         SUM(po.total_value)::numeric AS total_spend "
            "  FROM ${schema}.purchase_orders po "
            "  WHERE po.ordered_at >= now() - interval '365 days' "
            "  GROUP BY po.supplier_id "
            "), total AS ( "
            "  SELECT SUM(total_spend) AS grand_total FROM supplier_spend "
            ") "
            "SELECT RANK() OVER (ORDER BY ss.total_spend DESC) AS rank, "
            "       ss.supplier_id, s.supplier_name, ss.total_spend, "
            "       ROUND((ss.total_spend * 100.0 / NULLIF(t.grand_total, 0))::numeric, 2) AS pct_of_total, "
            "       CASE WHEN (ss.total_spend * 100.0 / NULLIF(t.grand_total, 0)) > 25 THEN 'high_risk' "
            "            WHEN (ss.total_spend * 100.0 / NULLIF(t.grand_total, 0)) > 15 THEN 'moderate_risk' "
            "            ELSE 'ok' END AS concentration_flag "
            "FROM supplier_spend ss "
            "JOIN ${schema}.suppliers s ON s.supplier_id = ss.supplier_id "
            "CROSS JOIN total t "
            "ORDER BY ss.total_spend DESC "
            "LIMIT 5"
        ),
        "params_schema": {},
        "tags": ["supply_chain", "supplier", "concentration", "risk"],
    },
    {
        "name": "lead_time_outliers",
        "description": (
            "Identify purchase orders with lead time (order to delivery) more "
            "than 2× the median lead time for that supplier-SKU combination. "
            "Highlights supplier reliability problems. Param $1=outlier "
            "multiplier (float, default 2.0)."
        ),
        "sql_template": (
            "WITH lt AS ( "
            "  SELECT po.po_id, po.supplier_id, po.sku, "
            "         (po.delivered_at::date - po.ordered_at::date) AS lead_time_days, "
            "         po.qty_ordered "
            "  FROM ${schema}.purchase_orders po "
            "  WHERE po.delivered_at IS NOT NULL "
            "    AND po.ordered_at IS NOT NULL "
            "    AND po.ordered_at >= now() - interval '180 days' "
            "), medians AS ( "
            "  SELECT supplier_id, sku, "
            "         PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY lead_time_days) AS median_lt "
            "  FROM lt "
            "  GROUP BY supplier_id, sku "
            "  HAVING COUNT(*) >= 3 "
            ") "
            "SELECT lt.po_id, lt.supplier_id, s.supplier_name, lt.sku, "
            "       lt.lead_time_days, "
            "       ROUND(m.median_lt::numeric, 1) AS median_lead_time, "
            "       ROUND((lt.lead_time_days / NULLIF(m.median_lt, 0))::numeric, 2) AS times_median, "
            "       lt.qty_ordered "
            "FROM lt "
            "JOIN medians m ON m.supplier_id = lt.supplier_id AND m.sku = lt.sku "
            "JOIN ${schema}.suppliers s ON s.supplier_id = lt.supplier_id "
            "WHERE lt.lead_time_days > $1 * m.median_lt "
            "ORDER BY times_median DESC"
        ),
        "params_schema": {"1": "float"},
        "tags": ["supply_chain", "lead_time", "outlier", "supplier"],
    },
    {
        "name": "inventory_turns_by_sku",
        "description": (
            "Annualised inventory turnover per SKU. Turns = COGS_365d / "
            "avg_inventory_value. High turns = efficient stock, low turns = "
            "slow movers / overstock. Param $1=minimum avg inventory value "
            "to include (numeric, filters out trivial SKUs)."
        ),
        "sql_template": (
            "WITH cogs AS ( "
            "  SELECT s.sku, "
            "         SUM(s.qty_sold * s.unit_cost)::numeric AS cogs_365d "
            "  FROM ${schema}.sales s "
            "  WHERE s.sold_at >= now() - interval '365 days' "
            "  GROUP BY s.sku "
            "), inv_avg AS ( "
            "  SELECT i.sku, "
            "         AVG(i.qty_on_hand * i.unit_cost)::numeric AS avg_inv_value "
            "  FROM ${schema}.inventory_snapshots i "
            "  WHERE i.snapshot_date >= CURRENT_DATE - interval '365 days' "
            "  GROUP BY i.sku "
            ") "
            "SELECT c.sku, p.product_name, p.category, "
            "       c.cogs_365d, "
            "       ROUND(ia.avg_inv_value::numeric, 2) AS avg_inventory_value, "
            "       ROUND((c.cogs_365d / NULLIF(ia.avg_inv_value, 0))::numeric, 2) AS inventory_turns, "
            "       ROUND((365.0 / NULLIF(c.cogs_365d / NULLIF(ia.avg_inv_value, 0), 0))::numeric, 1) AS days_inventory_outstanding, "
            "       CASE WHEN (c.cogs_365d / NULLIF(ia.avg_inv_value, 0)) >= 12 THEN 'fast' "
            "            WHEN (c.cogs_365d / NULLIF(ia.avg_inv_value, 0)) >= 4 THEN 'normal' "
            "            WHEN (c.cogs_365d / NULLIF(ia.avg_inv_value, 0)) >= 2 THEN 'slow' "
            "            ELSE 'dead' END AS velocity_class "
            "FROM cogs c "
            "JOIN inv_avg ia ON ia.sku = c.sku "
            "LEFT JOIN ${schema}.products p ON p.sku = c.sku "
            "WHERE ia.avg_inv_value >= $1 "
            "ORDER BY inventory_turns DESC"
        ),
        "params_schema": {"1": "float"},
        "tags": ["supply_chain", "inventory", "turnover", "sku"],
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Registry — vertical → template_name → skills
# ═══════════════════════════════════════════════════════════════════════════════

ALL_SKILLS: dict[str, list[dict]] = {
    "pharmacy_network": PHARMACY_SKILLS,
    "hotel_group": HOTEL_SKILLS,
    "bank": BANK_SKILLS,
    "healthcare": HEALTHCARE_SKILLS,
    "supply_chain": SUPPLY_CHAIN_SKILLS,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Seeder
# ═══════════════════════════════════════════════════════════════════════════════


def _get_engine() -> Engine:
    """Lazy import to avoid circular deps at module import time."""
    from db.session import get_sql_engine
    return get_sql_engine()


def _to_pg_array(tags: list[str]) -> str:
    """Postgres text[] literal."""
    return "{" + ",".join(f'"{t}"' for t in tags) + "}"


def seed_marketplace(engine: Optional[Engine] = None) -> dict:
    """
    Idempotent INSERT of 25 skills (5 verticals × 5) into
    ``dash.dash_skill_marketplace``.

    Returns:
        {
          "inserted": int,
          "skipped": int,
          "total": int,
          "by_vertical": {vertical: {"inserted": N, "skipped": M, "total": T}}
        }
    """
    eng = engine or _get_engine()

    insert_sql = text(
        """
        INSERT INTO dash.dash_skill_marketplace (
            name,
            description,
            sql_template,
            params_schema,
            template_name,
            source_project_slug,
            avg_judge_score,
            source_success_count,
            install_count,
            status,
            tags
        ) VALUES (
            :name,
            :description,
            :sql_template,
            CAST(:params_schema AS jsonb),
            :template_name,
            :source_project_slug,
            :avg_judge_score,
            :source_success_count,
            0,
            'active',
            CAST(:tags AS text[])
        )
        ON CONFLICT (name, template_name) DO NOTHING
        RETURNING id
        """
    )

    by_vertical: dict[str, dict] = {}
    total_inserted = 0
    total_skipped = 0
    total_skills = 0

    with eng.begin() as conn:
        for template_name, skills in ALL_SKILLS.items():
            v_inserted = 0
            v_skipped = 0
            total_skills += len(skills)

            for skill in skills:
                params = {
                    "name": skill["name"],
                    "description": skill["description"],
                    "sql_template": skill["sql_template"],
                    "params_schema": json.dumps(skill.get("params_schema", {})),
                    "template_name": template_name,
                    "source_project_slug": SOURCE_PROJECT_SLUG,
                    "avg_judge_score": DEFAULT_JUDGE_SCORE,
                    "source_success_count": DEFAULT_SUCCESS_COUNT,
                    "tags": _to_pg_array(skill.get("tags", [])),
                }
                try:
                    row = conn.execute(insert_sql, params).fetchone()
                    if row is not None:
                        v_inserted += 1
                        log.info(
                            "[marketplace.seed] inserted %s/%s id=%s",
                            template_name, skill["name"], row[0],
                        )
                    else:
                        v_skipped += 1
                        log.debug(
                            "[marketplace.seed] skipped (exists) %s/%s",
                            template_name, skill["name"],
                        )
                except Exception as e:
                    log.exception(
                        "[marketplace.seed] failed %s/%s err=%s",
                        template_name, skill["name"], e,
                    )
                    v_skipped += 1

            by_vertical[template_name] = {
                "inserted": v_inserted,
                "skipped": v_skipped,
                "total": len(skills),
            }
            total_inserted += v_inserted
            total_skipped += v_skipped

    result = {
        "inserted": total_inserted,
        "skipped": total_skipped,
        "total": total_skills,
        "by_vertical": by_vertical,
    }
    log.info("[marketplace.seed] done: %s", result)
    return result


def verify_seed(engine: Optional[Engine] = None) -> list[dict]:
    """Return per-template skill counts for inspection."""
    eng = engine or _get_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT template_name, COUNT(*) AS skill_count
                FROM dash.dash_skill_marketplace
                GROUP BY template_name
                ORDER BY template_name
                """
            )
        ).mappings().all()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    result = seed_marketplace()
    print(json.dumps(result, indent=2))
    print("\nMarketplace skill counts by template:")
    for row in verify_seed():
        print(f"  {row['template_name']:25s}  count={row['skill_count']}")
