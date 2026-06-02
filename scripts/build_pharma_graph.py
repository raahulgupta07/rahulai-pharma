#!/usr/bin/env python3
"""Build the CityPharma Apache AGE knowledge graph from articles + balance_stock.

Graph: 'citypharma'
Nodes : Article {code, brand, generic, category, indication, composition}
        Generic {name} · Category {name} · Indication {name} · Composition {name}
Edges : (Article)-[:HAS_GENERIC]->(Generic)
        (Article)-[:IN_CATEGORY]->(Category)
        (Article)-[:TREATS]->(Indication)
        (Article)-[:HAS_COMPOSITION]->(Composition)
        (Article)-[:SUBSTITUTE_OF]->(Article)   # same generic_name, derived

Stock stays relational (balance_stock, 106k rows) — joined by article_code at query time.
INTERACTS_WITH (LLM-extracted) is a separate v2 pass.

Run inside cp-api:  docker exec cp-api python /app/scripts/build_pharma_graph.py
Connects DIRECT to cp-db (service dash-db:5432) so LOAD + search_path persist in one session.
Idempotent: drops + recreates the graph.
"""
import os
import sys
import psycopg

SCHEMA = "proj_demo_citypharma"
ART = f"{SCHEMA}.citypharma_articles"
GRAPH = "citypharma"


def _clean(v) -> str:
    if v is None:
        return ""
    s = str(v).strip().replace("'", "’").replace("\\", " ")
    return s[:200]


def main() -> int:
    host = os.getenv("GRAPH_DB_HOST", "dash-db")          # direct to postgres, NOT pgbouncer
    port = int(os.getenv("GRAPH_DB_PORT", "5432"))
    user = os.getenv("DB_USER", "ai")
    db = os.getenv("DB_DATABASE", "ai")
    pw = os.getenv("DB_PASS", "")

    conn = psycopg.connect(host=host, port=port, user=user, dbname=db, password=pw, autocommit=True)
    cur = conn.cursor()
    cur.execute("LOAD 'age';")
    cur.execute('SET search_path = ag_catalog, "$user", public;')

    # fresh graph
    cur.execute("SELECT * FROM ag_catalog.drop_graph(%s, true);" if False else
                "SELECT count(*) FROM ag_catalog.ag_graph WHERE name = %s;", (GRAPH,))
    if cur.fetchone()[0]:
        cur.execute(f"SELECT * FROM ag_catalog.drop_graph('{GRAPH}', true);")
    cur.execute(f"SELECT * FROM ag_catalog.create_graph('{GRAPH}');")
    print(f"[graph] created '{GRAPH}'", flush=True)

    cur.execute(
        f"SELECT article_code, brand_name, generic_name, category, indication, composition "
        f"FROM {ART} WHERE article_code IS NOT NULL"
    )
    rows = cur.fetchall()
    print(f"[graph] {len(rows)} articles to load", flush=True)

    def cy(q: str):
        cur.execute(f"SELECT * FROM cypher('{GRAPH}', $$ {q} $$) AS (a agtype);")

    n = 0
    for code, brand, generic, cat, indic, comp in rows:
        b, g, c, i, cp = _clean(brand), _clean(generic), _clean(cat), _clean(indic), _clean(comp)
        parts = [f"MERGE (a:Article {{code: {int(code)}}}) SET a.brand='{b}', a.generic='{g}', a.category='{c}'"]
        if g:
            parts.append(f"MERGE (gn:Generic {{name:'{g}'}}) MERGE (a)-[:HAS_GENERIC]->(gn)")
        if c:
            parts.append(f"MERGE (ct:Category {{name:'{c}'}}) MERGE (a)-[:IN_CATEGORY]->(ct)")
        if i:
            parts.append(f"MERGE (ix:Indication {{name:'{i}'}}) MERGE (a)-[:TREATS]->(ix)")
        if cp:
            parts.append(f"MERGE (co:Composition {{name:'{cp}'}}) MERGE (a)-[:HAS_COMPOSITION]->(co)")
        cy(" ".join(parts) + " RETURN a")
        n += 1
        if n % 500 == 0:
            print(f"[graph]   {n}/{len(rows)} articles", flush=True)

    # SUBSTITUTE_OF: articles sharing a generic become mutual substitutes (2-hop made explicit)
    cy("""MATCH (a:Article)-[:HAS_GENERIC]->(g:Generic)<-[:HAS_GENERIC]-(b:Article)
          WHERE a.code <> b.code
          MERGE (a)-[:SUBSTITUTE_OF]->(b)
          RETURN count(*)""")
    print("[graph] SUBSTITUTE_OF edges derived", flush=True)

    # stats
    for lbl in ("Article", "Generic", "Category", "Indication", "Composition"):
        cur.execute(f'SELECT count(*) FROM "{GRAPH}"."{lbl}";')
        print(f"[stat] {lbl}: {cur.fetchone()[0]}", flush=True)
    for e in ("HAS_GENERIC", "IN_CATEGORY", "TREATS", "HAS_COMPOSITION", "SUBSTITUTE_OF"):
        try:
            cur.execute(f'SELECT count(*) FROM "{GRAPH}"."{e}";')
            print(f"[stat] {e}: {cur.fetchone()[0]}", flush=True)
        except Exception:
            pass

    cur.close()
    conn.close()
    print("[graph] DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
