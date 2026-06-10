"""Synthetic pharma DEMO SEED.

Loads realistic-looking pharmacy data (a drug catalog + per-store stock) into the
locked ``citypharma`` schema so a fresh / empty install demos out-of-box.

Two triggers, one code path:
  * cold boot  — ``app/main.py`` lifespan calls ``seed_demo(force=False)`` when the
                 project is empty (env ``DEMO_SEED_ON_EMPTY=1``, default on).
  * admin UI   — ``POST /api/projects/{slug}/seed-demo`` (button on Data Source).

It reuses the real ingest target (``df.to_sql`` into the project schema) and the
existing retrain pipeline (which profiles, trains, and rebuilds ``shop_flat``).
No training logic is duplicated here — training is kicked via an internal,
authenticated self-call to ``/api/projects/{slug}/retrain``.

Generated tables (columns chosen so the stock/catalog resolvers + pharma tools
+ build_shop_flat all match):
  * ``articles_clean``  : article_code, brand_name, generic_name, composition,
                          category, form, base_price
  * ``balance_stock``   : article_code, site_code, stock_qty, weighted_cost_price

CLI:  python scripts/seed_pharma_demo.py [--force] [--no-train]
"""
from __future__ import annotations

import logging
import os
import random
import sys
import time

logger = logging.getLogger("seed_pharma_demo")

SLUG = "citypharma"
N_ARTICLES = 200
N_STORES = 8

# 8 outlets across Myanmar (site_code -> display name)
STORES = [
    ("YGN01", "Yangon — Downtown"),
    ("YGN02", "Yangon — Hlaing"),
    ("YGN03", "Yangon — North Dagon"),
    ("MDY01", "Mandalay — Chanmyathazi"),
    ("MDY02", "Mandalay — Pyigyitagon"),
    ("NPT01", "Naypyitaw — Zabuthiri"),
    ("MWY01", "Mawlamyine — Central"),
    ("TGI01", "Taunggyi — Main"),
]

# (generic, category, form, strengths, brand_prefixes). Brand variants per generic
# share a generic_name -> they become each other's substitutes.
GENERICS = [
    ("Paracetamol", "Analgesic / Antipyretic", "Tablet", ["500mg", "650mg"], ["Panadol", "Calpol", "Tylenol", "Decolgen"]),
    ("Ibuprofen", "NSAID", "Tablet", ["200mg", "400mg"], ["Brufen", "Advil", "Nurofen"]),
    ("Amoxicillin", "Antibiotic (Penicillin)", "Capsule", ["250mg", "500mg"], ["Amoxil", "Moxikind", "Novamox"]),
    ("Amoxicillin + Clavulanate", "Antibiotic (Penicillin)", "Tablet", ["625mg", "1g"], ["Augmentin", "Clavam"]),
    ("Azithromycin", "Antibiotic (Macrolide)", "Tablet", ["250mg", "500mg"], ["Zithromax", "Azee", "Azithral"]),
    ("Ciprofloxacin", "Antibiotic (Quinolone)", "Tablet", ["250mg", "500mg"], ["Ciplox", "Cifran"]),
    ("Metronidazole", "Antibiotic / Antiprotozoal", "Tablet", ["200mg", "400mg"], ["Flagyl", "Metrogyl"]),
    ("Omeprazole", "Proton Pump Inhibitor", "Capsule", ["20mg", "40mg"], ["Omez", "Prilosec", "Losec"]),
    ("Pantoprazole", "Proton Pump Inhibitor", "Tablet", ["20mg", "40mg"], ["Pan", "Pantop", "Protonix"]),
    ("Esomeprazole", "Proton Pump Inhibitor", "Tablet", ["20mg", "40mg"], ["Nexium", "Esoz"]),
    ("Ranitidine", "H2 Blocker", "Tablet", ["150mg", "300mg"], ["Zantac", "Rantac"]),
    ("Metformin", "Antidiabetic (Biguanide)", "Tablet", ["500mg", "850mg", "1g"], ["Glucophage", "Glycomet"]),
    ("Glimepiride", "Antidiabetic (Sulfonylurea)", "Tablet", ["1mg", "2mg"], ["Amaryl", "Glimy"]),
    ("Gliclazide", "Antidiabetic (Sulfonylurea)", "Tablet", ["40mg", "80mg"], ["Diamicron", "Glizid"]),
    ("Amlodipine", "Antihypertensive (CCB)", "Tablet", ["5mg", "10mg"], ["Norvasc", "Amlong", "Amdepin"]),
    ("Losartan", "Antihypertensive (ARB)", "Tablet", ["25mg", "50mg"], ["Cozaar", "Losar"]),
    ("Telmisartan", "Antihypertensive (ARB)", "Tablet", ["40mg", "80mg"], ["Telma", "Micardis"]),
    ("Atenolol", "Antihypertensive (Beta-blocker)", "Tablet", ["25mg", "50mg"], ["Tenormin", "Aten"]),
    ("Enalapril", "Antihypertensive (ACE)", "Tablet", ["5mg", "10mg"], ["Vasotec", "Envas"]),
    ("Atorvastatin", "Lipid-lowering (Statin)", "Tablet", ["10mg", "20mg", "40mg"], ["Lipitor", "Atorva"]),
    ("Rosuvastatin", "Lipid-lowering (Statin)", "Tablet", ["5mg", "10mg", "20mg"], ["Crestor", "Rosuvas"]),
    ("Salbutamol", "Bronchodilator", "Inhaler", ["100mcg"], ["Ventolin", "Asthalin"]),
    ("Montelukast", "Anti-asthmatic (LTRA)", "Tablet", ["5mg", "10mg"], ["Singulair", "Montair"]),
    ("Cetirizine", "Antihistamine", "Tablet", ["5mg", "10mg"], ["Zyrtec", "Cetzine", "Alerid"]),
    ("Loratadine", "Antihistamine", "Tablet", ["10mg"], ["Claritin", "Lorfast"]),
    ("Levocetirizine", "Antihistamine", "Tablet", ["5mg"], ["Xyzal", "Levocet"]),
    ("Dexamethasone", "Corticosteroid", "Tablet", ["0.5mg", "4mg"], ["Decadron", "Dexona"]),
    ("Prednisolone", "Corticosteroid", "Tablet", ["5mg", "10mg"], ["Omnacortil", "Wysolone"]),
    ("Diclofenac", "NSAID", "Tablet", ["50mg", "75mg"], ["Voveran", "Voltaren"]),
    ("Aceclofenac", "NSAID", "Tablet", ["100mg"], ["Hifenac", "Zerodol"]),
    ("Tramadol", "Opioid Analgesic", "Capsule", ["50mg", "100mg"], ["Ultram", "Tramazac"]),
    ("Aspirin", "Antiplatelet / Analgesic", "Tablet", ["75mg", "150mg"], ["Ecosprin", "Disprin"]),
    ("Clopidogrel", "Antiplatelet", "Tablet", ["75mg"], ["Plavix", "Clopilet"]),
    ("Warfarin", "Anticoagulant", "Tablet", ["1mg", "5mg"], ["Coumadin", "Warf"]),
    ("Levothyroxine", "Thyroid Hormone", "Tablet", ["25mcg", "50mcg", "100mcg"], ["Eltroxin", "Thyronorm"]),
    ("Furosemide", "Diuretic (Loop)", "Tablet", ["40mg"], ["Lasix", "Frusenex"]),
    ("Hydrochlorothiazide", "Diuretic (Thiazide)", "Tablet", ["12.5mg", "25mg"], ["Esidrix", "Aquazide"]),
    ("Spironolactone", "Diuretic (K-sparing)", "Tablet", ["25mg", "50mg"], ["Aldactone", "Spiractin"]),
    ("Domperidone", "Antiemetic / Prokinetic", "Tablet", ["10mg"], ["Motilium", "Domstal"]),
    ("Ondansetron", "Antiemetic", "Tablet", ["4mg", "8mg"], ["Zofran", "Emeset"]),
    ("Loperamide", "Antidiarrheal", "Capsule", ["2mg"], ["Imodium", "Eldoper"]),
    ("ORS", "Oral Rehydration Salt", "Sachet", ["20.5g"], ["Electral", "ORS-L"]),
    ("Vitamin C", "Vitamin Supplement", "Tablet", ["500mg", "1000mg"], ["Limcee", "Celin"]),
    ("Vitamin D3", "Vitamin Supplement", "Tablet", ["1000IU", "60000IU"], ["Calcirol", "D-Rise"]),
    ("Vitamin B-Complex", "Vitamin Supplement", "Tablet", ["—"], ["Becosules", "Neurobion"]),
    ("Ferrous Sulfate", "Iron Supplement", "Tablet", ["200mg"], ["Feronia", "Fefol"]),
    ("Calcium + D3", "Mineral Supplement", "Tablet", ["500mg"], ["Shelcal", "Calcimax"]),
    ("Folic Acid", "Vitamin Supplement", "Tablet", ["5mg"], ["Folvite", "Fol-123"]),
    ("Insulin (Regular)", "Antidiabetic (Insulin)", "Injection", ["40IU/ml", "100IU/ml"], ["Actrapid", "Huminsulin"]),
    ("Hydroxychloroquine", "Antimalarial / DMARD", "Tablet", ["200mg"], ["Plaquenil", "HCQS"]),
    ("Artemether + Lumefantrine", "Antimalarial", "Tablet", ["80/480mg"], ["Coartem", "Lumerax"]),
    ("Albendazole", "Anthelmintic", "Tablet", ["400mg"], ["Zentel", "Bandy"]),
    ("Fluconazole", "Antifungal", "Tablet", ["150mg", "200mg"], ["Diflucan", "Forcan"]),
    ("Clotrimazole", "Antifungal (Topical)", "Cream", ["1%"], ["Candid", "Canesten"]),
    ("Acyclovir", "Antiviral", "Tablet", ["200mg", "400mg"], ["Zovirax", "Acivir"]),
    ("Doxycycline", "Antibiotic (Tetracycline)", "Capsule", ["100mg"], ["Vibramycin", "Doxt"]),
    ("Cefixime", "Antibiotic (Cephalosporin)", "Tablet", ["100mg", "200mg"], ["Suprax", "Taxim-O"]),
    ("Cephalexin", "Antibiotic (Cephalosporin)", "Capsule", ["250mg", "500mg"], ["Keflex", "Sporidex"]),
    ("Diazepam", "Anxiolytic / Sedative", "Tablet", ["5mg", "10mg"], ["Valium", "Calmpose"]),
    ("Amitriptyline", "Antidepressant (TCA)", "Tablet", ["10mg", "25mg"], ["Elavil", "Tryptomer"]),
    ("Sertraline", "Antidepressant (SSRI)", "Tablet", ["50mg", "100mg"], ["Zoloft", "Serta"]),
    ("Gabapentin", "Anticonvulsant / Neuropathic", "Capsule", ["100mg", "300mg"], ["Neurontin", "Gabapin"]),
]


def generate_demo_dataframes():
    """Build the two synthetic source tables. Deterministic (fixed RNG seed)."""
    import pandas as pd  # local import — keeps the module import-light at boot

    rng = random.Random(20260610)

    articles = []
    code = 10_000_001
    # one SKU per (brand x strength) — realistic catalog (same brand at 250mg & 500mg)
    skus = []
    for generic, category, form, strengths, brands in GENERICS:
        for brand in brands:
            for strength in strengths:
                skus.append((generic, category, form, strength, brand))
    rng.shuffle(skus)
    for generic, category, form, strength, brand in skus:
        sfx = "" if strength == "—" else f" {strength}"
        base_price = rng.choice([350, 500, 750, 900, 1200, 1500, 2000, 2500, 3500, 5000])
        articles.append({
            "article_code": str(code),
            "brand_name": f"{brand}{sfx}",
            "generic_name": generic,
            "composition": f"{generic}{sfx}".strip(),
            "category": category,
            "form": form,
            "base_price": base_price,
        })
        code += 1
        if len(articles) >= N_ARTICLES:
            break

    articles_df = pd.DataFrame(articles)

    # per (article, store) stock — every article in every store, ~14% out of stock
    stock = []
    for a in articles:
        for site_code, _name in STORES[:N_STORES]:
            if rng.random() < 0.14:
                qty = 0
            else:
                qty = rng.randint(1, 600)
            cost = round(a["base_price"] * rng.uniform(0.62, 0.78), 2)
            stock.append({
                "article_code": a["article_code"],
                "site_code": site_code,
                "stock_qty": qty,
                "weighted_cost_price": cost,
            })
    stock_df = pd.DataFrame(stock)
    return {"articles_clean": articles_df, "balance_stock": stock_df}


def _project_is_empty(engine, schema: str) -> bool:
    """A project is 'empty' when it has no source tables in its schema yet."""
    from sqlalchemy import inspect, text
    try:
        names = inspect(engine).get_table_names(schema=schema)
        names = [n for n in names if n not in ("shop_flat",)]
        if not names:
            return True
        # has table objects but every one is empty?
        with engine.connect() as c:
            for n in names:
                try:
                    cnt = c.execute(text(f'SELECT COUNT(*) FROM "{schema}"."{n}"')).scalar() or 0
                    if cnt > 0:
                        return False
                except Exception:
                    continue
        return True
    except Exception:
        return True


def _ensure_project_row(slug: str):
    """Make sure the locked project row exists in public.dash_projects.

    Fixes the fresh-install '404 Project not found' (no dash_projects row for the
    locked slug). Idempotent — does nothing when the row already exists."""
    from sqlalchemy import text
    from db.session import get_write_engine
    try:
        with get_write_engine().begin() as c:
            exists = c.execute(text("SELECT 1 FROM public.dash_projects WHERE slug=:s"), {"s": slug}).fetchone()
            if exists:
                return False
            uid = c.execute(text(
                "SELECT id FROM public.dash_users WHERE username=:u OR role IN ('super','admin') "
                "ORDER BY (username=:u) DESC, id ASC LIMIT 1"
            ), {"u": os.getenv("SUPER_ADMIN", "admin")}).scalar()
            c.execute(text(
                "INSERT INTO public.dash_projects (user_id, slug, name, agent_name, agent_role, schema_name) "
                "VALUES (:uid, :s, :name, :agent, :role, :schema) ON CONFLICT (slug) DO NOTHING"
            ), {"uid": uid, "s": slug, "name": "CityAgent Pharma",
                "agent": "CityAgent Pharma Analyst", "role": "pharmacy data analyst", "schema": slug})
        logger.info("seed: created locked dash_projects row for %s", slug)
        return True
    except Exception as e:
        logger.warning("seed: ensure_project_row failed: %s", e)
        return False


def _mint_super_token(ttl: int = 3600):
    """Mint a short-lived super-admin token (DB-backed, cross-worker) for the
    internal retrain self-call."""
    import secrets
    from sqlalchemy import text
    from db.session import get_write_engine
    try:
        with get_write_engine().begin() as c:
            row = c.execute(text(
                "SELECT id, username FROM public.dash_users WHERE username=:u OR role IN ('super','admin') "
                "ORDER BY (username=:u) DESC, id ASC LIMIT 1"
            ), {"u": os.getenv("SUPER_ADMIN", "admin")}).fetchone()
            if not row:
                return None
            tok = secrets.token_urlsafe(32)
            exp = int(time.time()) + ttl
            c.execute(text(
                "INSERT INTO public.dash_tokens (token, user_id, username, expiry) VALUES (:t, :uid, :u, :e)"
            ), {"t": tok, "uid": row[0], "u": row[1], "e": exp})
        return tok
    except Exception as e:
        logger.warning("seed: mint token failed: %s", e)
        return None


def _trigger_training(slug: str, wait: float = 8.0):
    """Kick the existing retrain pipeline via an internal authenticated self-call.

    Runs in a background thread: waits for the HTTP server to accept connections
    (lifespan startup runs BEFORE serving), then POSTs /retrain?force=1. Tables
    are already loaded, so even if this fails the demo is visible (untrained) and
    the user can click 'Train all'."""
    import threading

    def _run():
        time.sleep(wait)
        token = _mint_super_token()
        if not token:
            logger.warning("seed: no token — skipping auto-train (click 'Train all')")
            return
        port = os.getenv("PORT", "8000")
        url = f"http://127.0.0.1:{port}/api/projects/{slug}/retrain?force=1"
        import urllib.request
        for attempt in range(6):
            try:
                req = urllib.request.Request(
                    url, data=b"{}", method="POST",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=30).read()
                logger.info("seed: retrain kicked for %s", slug)
                return
            except Exception as e:
                if attempt == 5:
                    logger.warning("seed: retrain self-call failed after retries: %s", e)
                time.sleep(5)

    threading.Thread(target=_run, daemon=True, name=f"seed-train-{slug}").start()


def seed_demo(slug: str = SLUG, force: bool = False, train: bool = True) -> dict:
    """Load synthetic pharma demo data into the locked project.

    force=False : no-op when the project already has data (cold-boot safe).
    force=True  : REPLACE existing source tables with the demo set.
    train       : kick the retrain pipeline (background) once tables are loaded.

    Returns {ok, seeded, skipped, tables, rows, trained_kicked, note}.
    """
    from db.session import create_project_schema, get_write_engine

    schema = create_project_schema(slug)
    engine = get_write_engine()

    if not force and not _project_is_empty(engine, schema):
        return {"ok": True, "seeded": False, "skipped": True,
                "note": "project already has data — skipped (use force=true to replace)"}

    _ensure_project_row(slug)

    dfs = generate_demo_dataframes()
    loaded, total_rows = [], 0
    for name, df in dfs.items():
        # all-string keys avoid the 1E+12 article_code corruption class
        if "article_code" in df.columns:
            df["article_code"] = df["article_code"].astype(str)
        df.to_sql(name, engine, schema=schema, if_exists="replace", index=False)
        loaded.append(name)
        total_rows += len(df)
        logger.info("seed: loaded %s.%s (%d rows)", schema, name, len(df))

    # pharma Brain seeds (aliases, dosage forms, KPIs) — idempotent
    try:
        from dash.learning.seed_loader import load_seeds_for_domain
        load_seeds_for_domain(slug, "pharma")
    except Exception as e:
        logger.debug("seed: brain seeds skipped: %s", e)

    trained_kicked = False
    if train:
        _trigger_training(slug)
        trained_kicked = True

    return {"ok": True, "seeded": True, "skipped": False,
            "tables": loaded, "rows": total_rows, "trained_kicked": trained_kicked,
            "note": "demo data loaded" + (" — training kicked (background)" if trained_kicked else "")}


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    force = "--force" in argv
    train = "--no-train" not in argv
    res = seed_demo(SLUG, force=force, train=train)
    print(res)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    # allow running from repo root: python scripts/seed_pharma_demo.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main(sys.argv[1:]))
