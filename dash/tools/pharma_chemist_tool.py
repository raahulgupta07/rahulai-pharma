"""Pharma Chemist tools for the CityPharma Analyst — the clinical/pharmacist brain.

NMR-chemist analog (Anthropic "Claude as a chemist") for pharma RETAIL:
forward = drug -> full clinical profile, inverse = symptom/indication -> drugs.
Pure relational over the catalog clinical columns (generic_name, composition,
indication, dosage, side_effect, category) — NO AGE dependency, survives any
cp-db recreate. Every answer returns its SOURCE ROWS for pharmacist audit.

Own read-only direct connection to cp-db (service 'dash-db'). Disable with
PHARMA_GRAPH_DISABLED=1. Table names auto-detected (data lands as *_07052026).
"""
from __future__ import annotations

import os
import logging

log = logging.getLogger("dash.pharma_chemist")

SCHEMA = "citypharma"

# clinical columns the chemist reasons over
_CAT_COLS = ("brand_name", "generic_name", "composition", "category",
             "indication", "dosage", "side_effect", "article_code")
_STOCK_COLS = ("article_code", "stock_qty", "site_code", "weighted_cost_price")


def _conn():
    import psycopg
    c = psycopg.connect(
        host=os.getenv("GRAPH_DB_HOST", "dash-db"),
        port=int(os.getenv("GRAPH_DB_PORT", "5432")),
        user=os.getenv("DB_USER", "ai"),
        dbname=os.getenv("DB_DATABASE", "ai"),
        password=os.getenv("DB_PASS", ""),
        connect_timeout=8,
        autocommit=True,
    )
    cur = c.cursor()
    cur.execute("SET statement_timeout = '20s';")
    cur.execute(f'SET search_path = "{SCHEMA}", public;')
    return c, cur


def _find_tables(cur):
    """Catalog (generic_name+indication) + stock (stock_qty+site_code) tables,
    both resolved to the CURRENT upload (latest dash_table_metadata.updated_at)
    via the shared TTL-cached resolver — keeps this tool in sync with the shop
    tool and the Outlets picker."""
    from dash.tools.table_sync import latest_table, STOCK_COLS, INDICATION_COLS
    cat = latest_table(cur, SCHEMA, INDICATION_COLS)
    stock = latest_table(cur, SCHEMA, STOCK_COLS)
    return cat, stock


def _cat_table(cur):
    cat, _ = _find_tables(cur)
    if not cat:
        raise RuntimeError("no catalog table (generic_name+indication) found")
    return f'"{SCHEMA}"."{cat}"'


def _stock_join(cur, qtbl):
    """Return (in_stock_bool_expr_subquery) — total units across all sites per article_code."""
    _, stock = _find_tables(cur)
    if not stock:
        return None
    return f'"{SCHEMA}"."{stock}"'


def drug_profile(name: str = "", limit: int = 5) -> dict:
    """Full clinical profile of a medicine by brand OR generic name.

    name: brand name, partial name, or salt (e.g. 'panadol', 'amoxicillin').
    Returns composition, what it treats (indication), dosage, side effects,
    category, and whether it's in stock — with the source article_code for audit.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "chemist tools disabled"}
    if not name or not name.strip():
        return {"ok": False, "error": "provide a medicine name or salt"}
    q = f"%{name.strip()}%"
    try:
        c, cur = _conn()
        try:
            tq = _cat_table(cur)
            stock = _stock_join(cur, tq)
            cur.execute(
                f"""SELECT article_code, brand_name, generic_name, composition,
                           category, indication, dosage, side_effect
                    FROM {tq}
                    WHERE brand_name ILIKE %s OR generic_name ILIKE %s
                    ORDER BY (brand_name ILIKE %s) DESC, brand_name
                    LIMIT %s""",
                (q, q, q, int(limit)))
            rows = cur.fetchall()
            if not rows:
                return {"ok": True, "query": name, "count": 0, "results": []}
            codes = [r[0] for r in rows if r[0] is not None]
            in_stock = {}
            if stock and codes:
                ph = ",".join("%s" for _ in codes)
                # STOCK.article_code is TEXT; query with text, key result by int.
                cur.execute(
                    f"""SELECT article_code, COALESCE(SUM(stock_qty),0)
                        FROM {stock} WHERE article_code IN ({ph})
                        GROUP BY article_code""", tuple(str(c) for c in codes))
                for r in cur.fetchall():
                    try: in_stock[int(r[0])] = int(r[1])
                    except (TypeError, ValueError): in_stock[r[0]] = int(r[1])
            results = []
            for ac, brand, gen, comp, cat, ind, dose, se in rows:
                units = in_stock.get(ac, 0)
                results.append({
                    "article_code": ac,
                    "brand": (brand or "").strip(),
                    "generic": (gen or "").strip(),
                    "composition": (comp or "").strip(),
                    "category": (cat or "").strip(),
                    "indication": (ind or "").strip(),
                    "dosage": (dose or "").strip(),
                    "side_effect": (se or "").strip(),
                    "in_stock": units > 0,
                    "stock_units": units,
                    "_source": f"article_code={ac}",
                })
            return {"ok": True, "query": name, "count": len(results), "results": results}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"drug_profile failed: {e}")
        return {"ok": False, "error": str(e)[:300]}


def substitutes(name: str = "", in_stock_only: bool = False, limit: int = 20) -> dict:
    """Find substitutes for a medicine — drugs sharing the same generic OR composition.

    name: brand or generic of the drug that's out / being replaced.
    in_stock_only: if true, only return substitutes that currently have stock.
    Returns substitute brands ranked by stock, each with WHY (matched generic),
    plus the source article_codes for pharmacist audit.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "chemist tools disabled"}
    if not name or not name.strip():
        return {"ok": False, "error": "provide a medicine name"}
    q = f"%{name.strip()}%"
    try:
        c, cur = _conn()
        try:
            tq = _cat_table(cur)
            stock = _stock_join(cur, tq)
            # resolve the target's generic_name
            cur.execute(
                f"""SELECT generic_name FROM {tq}
                    WHERE (brand_name ILIKE %s OR generic_name ILIKE %s)
                      AND generic_name IS NOT NULL AND generic_name <> ''
                    LIMIT 1""", (q, q))
            row = cur.fetchone()
            if not row:
                return {"ok": True, "query": name, "generic": None, "count": 0, "results": []}
            generic = row[0]
            stock_sel = (f"COALESCE((SELECT SUM(stock_qty) FROM {stock} s "
                         f"WHERE s.article_code = a.article_code::text),0)") if stock else "0"
            cur.execute(
                f"""SELECT a.article_code, a.brand_name, a.composition, a.category,
                           {stock_sel} AS units
                    FROM {tq} a
                    WHERE a.generic_name = %s AND a.brand_name NOT ILIKE %s
                    ORDER BY units DESC, a.brand_name
                    LIMIT %s""",
                (generic, q, int(limit)))
            results = []
            for ac, brand, comp, cat, units in cur.fetchall():
                u = int(units or 0)
                if in_stock_only and u <= 0:
                    continue
                results.append({
                    "article_code": ac,
                    "brand": (brand or "").strip(),
                    "composition": (comp or "").strip(),
                    "category": (cat or "").strip(),
                    "in_stock": u > 0,
                    "stock_units": u,
                    "why": f"same generic '{generic}'",
                    "_source": f"article_code={ac}",
                })
            return {"ok": True, "query": name, "generic": generic,
                    "count": len(results), "results": results}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"substitutes failed: {e}")
        return {"ok": False, "error": str(e)[:300]}


# English → Burmese symptom/condition synonyms — indication column is Burmese.
# Each English key maps to Burmese substrings that appear in real indication text.
_SYMPTOM_MM = {
    "fever": ["ဖျား"], "temperature": ["ဖျား"], "headache": ["ခေါင်းကိုက်"],
    "pain": ["နာကျင်", "ကိုက်"], "ache": ["ကိုက်", "နာ"], "cough": ["ချောင်းဆိုး"],
    "cold": ["အအေးမိ", "နှာစေး"], "flu": ["တုပ်ကွေး"], "runny nose": ["နှာစေး"],
    "toothache": ["သွားကိုက်"], "tooth": ["သွား"], "sore throat": ["လည်ချောင်းနာ"],
    "diabetes": ["ဆီးချို"], "sugar": ["ဆီးချို", "သကြား"], "blood pressure": ["သွေးတိုး"],
    "hypertension": ["သွေးတိုး"], "diarrhea": ["ဝမ်းလျှော"], "diarrhoea": ["ဝမ်းလျှော"],
    "vomiting": ["အန်"], "nausea": ["အန်", "ပျို့"], "gastric": ["အစာအိမ်"],
    "stomach": ["အစာအိမ်", "ဝမ်း"], "allergy": ["ဓာတ်မတည့်", "အလာဂျီ"],
    "cancer": ["ကင်ဆာ"], "inflammation": ["ရောင်ရမ်း"], "swelling": ["ရောင်ရမ်း"],
    "infection": ["ပိုးဝင်", "ပြည်တည်"], "asthma": ["ရင်ကျပ်", "ပန်းနာ"],
    "heart": ["နှလုံး"], "liver": ["အသည်း"], "dizziness": ["မူး"],
    "muscle": ["ကြွက်သား"], "joint": ["အဆစ်"], "wound": ["ဒဏ်ရာ", "အနာ"],
}


def _expand_symptom_terms(symptom: str) -> list:
    """Return the original term plus any mapped Burmese synonyms (dedup)."""
    s = symptom.lower().strip()
    terms = [symptom.strip()]
    for en, mm in _SYMPTOM_MM.items():
        if en in s:
            terms.extend(mm)
    # dedup preserving order
    seen, out = set(), []
    for t in terms:
        if t and t not in seen:
            seen.add(t); out.append(t)
    return out


def indication_search(symptom: str = "", in_stock_only: bool = False, limit: int = 20) -> dict:
    """INVERSE lookup: given a symptom/condition, find candidate medicines.

    symptom: free text condition (e.g. 'fever', 'hypertension', 'cough').
    Searches the indication column. Returns matching drugs (brand + generic +
    dosage + stock) with the source article_code for audit. This is the
    spectrum->structure analog: symptom -> drug set.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "chemist tools disabled"}
    if not symptom or not symptom.strip():
        return {"ok": False, "error": "provide a symptom or condition"}
    # Indication data is Burmese — translate common English symptom terms so an
    # English query ("fever") still matches the Burmese column ("ဖျား"). Search
    # the original term OR any mapped Burmese synonyms (substring match on each).
    terms = _expand_symptom_terms(symptom.strip())
    try:
        c, cur = _conn()
        try:
            tq = _cat_table(cur)
            stock = _stock_join(cur, tq)
            stock_sel = (f"COALESCE((SELECT SUM(stock_qty) FROM {stock} s "
                         f"WHERE s.article_code = a.article_code::text),0)") if stock else "0"
            _where = " OR ".join(["a.indication ILIKE %s"] * len(terms))
            cur.execute(
                f"""SELECT a.article_code, a.brand_name, a.generic_name,
                           a.indication, a.dosage, a.category, {stock_sel} AS units
                    FROM {tq} a
                    WHERE {_where}
                    ORDER BY units DESC, a.brand_name
                    LIMIT %s""", (*[f"%{t}%" for t in terms], int(limit) * 2))
            results = []
            for ac, brand, gen, ind, dose, cat, units in cur.fetchall():
                u = int(units or 0)
                if in_stock_only and u <= 0:
                    continue
                results.append({
                    "article_code": ac,
                    "brand": (brand or "").strip(),
                    "generic": (gen or "").strip(),
                    "indication": (ind or "").strip(),
                    "dosage": (dose or "").strip(),
                    "category": (cat or "").strip(),
                    "in_stock": u > 0,
                    "stock_units": u,
                    "_source": f"article_code={ac}",
                })
                if len(results) >= int(limit):
                    break
            return {"ok": True, "symptom": symptom, "count": len(results), "results": results}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"indication_search failed: {e}")
        return {"ok": False, "error": str(e)[:300]}


def interaction_check(drug_a: str = "", drug_b: str = "") -> dict:
    """Flag possible concerns when giving two medicines together.

    drug_a, drug_b: brand or generic names. Returns each drug's composition +
    side_effect + indication so the pharmacist can judge overlap/duplication.
    Heuristic only (no curated interaction DB): flags SAME generic (duplicate
    therapy) and overlapping side-effect keywords. Always returns source rows.
    """
    if os.getenv("PHARMA_GRAPH_DISABLED") == "1":
        return {"ok": False, "error": "chemist tools disabled"}
    if not drug_a.strip() or not drug_b.strip():
        return {"ok": False, "error": "provide two medicine names"}
    try:
        c, cur = _conn()
        try:
            tq = _cat_table(cur)

            def _one(nm):
                cur.execute(
                    f"""SELECT article_code, brand_name, generic_name, composition,
                               side_effect, indication
                        FROM {tq}
                        WHERE brand_name ILIKE %s OR generic_name ILIKE %s
                        ORDER BY (brand_name ILIKE %s) DESC LIMIT 1""",
                    (f"%{nm.strip()}%", f"%{nm.strip()}%", f"%{nm.strip()}%"))
                r = cur.fetchone()
                if not r:
                    return None
                return {"article_code": r[0], "brand": (r[1] or "").strip(),
                        "generic": (r[2] or "").strip(), "composition": (r[3] or "").strip(),
                        "side_effect": (r[4] or "").strip(), "indication": (r[5] or "").strip()}

            a = _one(drug_a)
            b = _one(drug_b)
            if not a or not b:
                return {"ok": True, "found": False,
                        "note": f"could not resolve {'A' if not a else 'B'}",
                        "a": a, "b": b}
            flags = []
            if a["generic"] and a["generic"].lower() == b["generic"].lower():
                flags.append(f"DUPLICATE THERAPY — both are '{a['generic']}'")
            # crude side-effect keyword overlap
            sa = {w for w in a["side_effect"].lower().replace(",", " ").split() if len(w) > 4}
            sb = {w for w in b["side_effect"].lower().replace(",", " ").split() if len(w) > 4}
            overlap = sorted(sa & sb)[:6]
            if overlap:
                flags.append(f"shared side-effect terms: {', '.join(overlap)}")
            return {"ok": True, "found": True, "flags": flags,
                    "severity": "review" if flags else "no obvious flag",
                    "a": a, "b": b,
                    "_audit": "heuristic only — confirm against a clinical interaction reference"}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"interaction_check failed: {e}")
        return {"ok": False, "error": str(e)[:300]}
