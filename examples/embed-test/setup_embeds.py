#!/usr/bin/env python3
"""Create N store-bound embeds for the parallel embed load test.

One embed per store (bound_scope_id = site_code) — that is how the embed path
scopes an answer to a single outlet (the per-embed binding always overrides the
session, app/embed_public.py). Idempotent: an embed named `embedtest-<store>` is
reused, not duplicated. Run INSIDE cp-api (has DB creds): it has psycopg + env.

  docker cp examples/embed-test/setup_embeds.py cp-api:/tmp/
  docker exec -e N_STORES=20 -e RESPONSE_STYLE=analyst cp-api python /tmp/setup_embeds.py
  docker cp cp-api:/tmp/embed_fixtures.json examples/embed-test/embed_fixtures.json

RESPONSE_STYLE=analyst → unmasked numbers (verify real stock). 'consumer' → masked
([banded]) like the production widget. ALLOWED_ORIGIN must match what the test sends.
"""
import os, json, secrets, psycopg

SLUG = os.getenv("LOCKED_PROJECT_SLUG", "citypharma")
N = int(os.getenv("N_STORES", "20"))
STYLE = os.getenv("RESPONSE_STYLE", "analyst")        # analyst | consumer
INTENT = os.getenv("BOUND_INTENT", "private")         # private=unmasked numbers (test) | public/network=masked
ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:3000")
SCHEMA = os.getenv("STORE_SCHEMA", SLUG)              # data tables live in the slug schema
STOCK_TABLE = os.getenv("STOCK_TABLE", "balance_stock_07052026")


def _conn():
    return psycopg.connect(host=os.getenv("DB_HOST"), dbname=os.getenv("DB_DATABASE"),
                           user=os.getenv("DB_USER"), password=os.getenv("DB_PASS"))


def main():
    out = {}
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT site_code FROM {SCHEMA}.{STOCK_TABLE} "
            "WHERE site_code IS NOT NULL AND site_code <> '' ORDER BY 1 LIMIT %s", (N,))
        stores = [r[0] for r in cur.fetchall()]
        print(f"{len(stores)} stores")

        # existing test embeds → reuse
        cur.execute("SELECT name, embed_id, public_key, bound_scope_id "
                    "FROM public.dash_agent_embeds WHERE name LIKE 'embedtest-%'")
        existing = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

        for s in stores:
            name = f"embedtest-{s}"
            if name in existing:
                eid, pk, _ = existing[name]
                # make sure binding + origin + enabled are right (idempotent fix-up)
                cur.execute(
                    "UPDATE public.dash_agent_embeds SET bound_scope_id=%s, enabled=true, "
                    "response_style=%s, bound_intent=%s, allowed_origins=%s, rate_limit_per_min=120, "
                    "auth_mode='public', max_reply_chars=800 WHERE embed_id=%s",
                    (s, STYLE, INTENT, [ORIGIN], eid))
                out[s] = {"embed_id": eid, "public_key": pk}
                continue
            eid = "emb_" + secrets.token_urlsafe(16)[:22]
            pk = "pub_" + secrets.token_urlsafe(24)[:32]
            cur.execute(
                "INSERT INTO public.dash_agent_embeds "
                "(embed_id, project_slug, public_key, secret_key_hash, name, allowed_origins, "
                " auth_mode, rate_limit_per_min, enabled, bound_scope_id, bound_intent, "
                " bound_role, response_style, max_reply_chars, status) "
                "VALUES (%s,%s,%s,'',%s,%s,'public',120,true,%s,%s,'staff',%s,800,'active')",
                (eid, SLUG, pk, name, [ORIGIN], s, INTENT, STYLE))
            out[s] = {"embed_id": eid, "public_key": pk}
        c.commit()

    fixtures = {"origin": ORIGIN, "response_style": STYLE, "embeds": out}
    with open("/tmp/embed_fixtures.json", "w") as f:
        json.dump(fixtures, f, indent=2)
    print(f"wrote /tmp/embed_fixtures.json — {len(out)} store-bound embeds (style={STYLE})")


if __name__ == "__main__":
    main()
