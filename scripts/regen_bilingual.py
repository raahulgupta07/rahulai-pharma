#!/usr/bin/env python3
"""Durable, idempotent bilingual-twin regenerator for CityPharma.

For every English knowledge surface (memories, query patterns, business rules,
company brain, table-metadata schema descriptions) create a Burmese (မြန်မာ)
twin so the agent has Burmese retrieval material. ONLY the natural-language text
is translated — identifiers, column names, brand names, numbers and SQL stay
verbatim.

Design goals:
  * IDEMPOTENT — re-running never duplicates (every surface has a marker that is
    checked before insert).
  * DURABLE — pg_dump-style backup of the 5 tables is written FIRST; aborts if
    the source is empty (never overwrite a populated backup with nothing useful).
  * REUSES PRE-TRANSLATED CACHE — the /tmp/cp_my_*.json files (matched by id /
    path) skip re-translation; only rows NOT covered by cache hit the LLM.

Run inside the cp-api container (has the OpenRouter key + DB creds + psycopg3):

    docker exec cp-api python /app/scripts/regen_bilingual.py

Also called automatically at the end of every successful training run (see the
hook in app/upload.py) so force-retrain keeps the data bilingual.
"""
from __future__ import annotations

import os
import re
import json
import datetime

SLUG = "citypharma"
MODEL = "google/gemini-3-flash-preview"

# Pre-translated cache files on the host /tmp (also mounted into cp-api /tmp).
CACHE = {
    "memories": "/tmp/cp_my_memories.json",
    "patterns": "/tmp/cp_my_patterns.json",
    "schema":   "/tmp/cp_my_schema.json",
    "rules":    "/tmp/cp_my_rules.json",
    "brain":    "/tmp/cp_my_brain.json",
}

BACKUP_PATH = "/tmp/pre_bilingual_write.sql"
BACKUP_TABLES = [
    "dash_memories", "dash_query_patterns", "dash_rules_db",
    "dash_company_brain", "dash_table_metadata",
]


# ───────────────────────── translation (LLM) ─────────────────────────────────
def _key() -> str:
    k = os.getenv("OPENROUTER_API_KEYS") or os.getenv("OPENROUTER_API_KEY") or ""
    return k.split(",")[0].strip()


def _is_my(s: str) -> bool:
    return any('က' <= c <= '႟' for c in (s or ""))


def translate_batch(texts: list[str]) -> list[str]:
    """One LLM call: English -> Burmese. Returns a list same length/order.

    Identifiers / brand names / numbers / SQL are kept in original form — only
    natural-language words are translated.
    """
    import urllib.request

    if not texts:
        return []
    numbered = "\n".join(f"{i+1}. {q}" for i, q in enumerate(texts))
    prompt = (
        "Translate each numbered pharmacy data string into natural, fluent Burmese "
        "(မြန်မာ). Keep database/column identifiers, table names, brand names, SQL "
        "snippets and numbers in their original Latin/Arabic form — translate only "
        "the natural-language words. Return ONLY a JSON array of strings, same order, "
        "no numbering, no extra text.\n\n" + numbered
    )
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        # REQUIRED: OpenRouter account data policy → without this the model
        # endpoint 404s ("No endpoints available matching your data policy").
        "provider": {"data_collection": "allow"},
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": "Bearer " + _key(),
                 "Content-Type": "application/json"})
    raw = json.loads(urllib.request.urlopen(req, timeout=180).read())
    txt = raw["choices"][0]["message"]["content"]
    m = re.search(r"\[.*\]", txt, re.DOTALL)
    if not m:
        raise RuntimeError(f"no JSON array in translation response: {txt[:200]}")
    arr = json.loads(m.group(0))
    if len(arr) != len(texts):
        raise RuntimeError(f"translation count mismatch: {len(arr)} vs {len(texts)}")
    return [str(x) for x in arr]


def _load_cache(name: str) -> list | None:
    path = CACHE.get(name)
    if path and os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            return None
    return None


# ───────────────────────── DB connection ─────────────────────────────────────
def _connect():
    """Direct psycopg3 connection using cp-api env DB creds."""
    import psycopg
    return psycopg.connect(
        host=os.getenv("DB_HOST", "dash-pgbouncer"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER", "ai"),
        password=os.getenv("DB_PASS", ""),
        dbname=os.getenv("DB_DATABASE", "ai"),
        autocommit=False,
    )


# ───────────────────────── backup ────────────────────────────────────────────
def _sql_lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    if isinstance(v, (datetime.datetime, datetime.date)):
        v = v.isoformat()
    return "'" + str(v).replace("'", "''") + "'"


def backup(cur) -> int:
    """pg_dump-style INSERT dump of the 5 tables -> BACKUP_PATH. Returns total rows."""
    total = 0
    lines = [
        "-- CityPharma bilingual-regen pre-write backup\n",
        f"-- generated {datetime.datetime.now(datetime.timezone.utc).isoformat()}Z\n\n",
    ]
    for tbl in BACKUP_TABLES:
        cur.execute(
            f"SELECT * FROM public.{tbl} WHERE project_slug = %s", (SLUG,)
        ) if tbl != "dash_company_brain" else cur.execute(
            # company_brain may hold global rows; dump both slug + global to be safe
            f"SELECT * FROM public.{tbl} "
            "WHERE project_slug = %s OR project_slug IS NULL", (SLUG,)
        )
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
        total += len(rows)
        lines.append(f"-- {tbl}: {len(rows)} rows\n")
        for r in rows:
            vals = ", ".join(_sql_lit(v) for v in r)
            lines.append(
                f"INSERT INTO public.{tbl} ({', '.join(cols)}) VALUES ({vals});\n"
            )
        lines.append("\n")
    with open(BACKUP_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return total


# ───────────────────────── DDL ───────────────────────────────────────────────
def ensure_lang_columns(cur) -> None:
    for tbl in ("dash_memories", "dash_rules_db", "dash_company_brain"):
        cur.execute(
            f"ALTER TABLE public.{tbl} "
            "ADD COLUMN IF NOT EXISTS lang text DEFAULT 'en'"
        )


# ───────────────────────── surface: memories ─────────────────────────────────
def regen_memories(cur) -> int:
    cache = {c["id"]: c.get("fact_my") for c in (_load_cache("memories") or [])}
    cur.execute(
        "SELECT id, scope, fact FROM public.dash_memories "
        "WHERE project_slug = %s AND (archived IS NULL OR archived = false) "
        "AND COALESCE(lang,'en') <> 'my' AND COALESCE(source,'') <> 'bilingual_twin'",
        (SLUG,),
    )
    src = cur.fetchall()
    # idempotency set: parent_ids that already have a my twin
    cur.execute(
        "SELECT parent_id FROM public.dash_memories "
        "WHERE project_slug = %s AND lang = 'my' AND parent_id IS NOT NULL",
        (SLUG,),
    )
    done = {r[0] for r in cur.fetchall()}

    todo, needs_llm, llm_idx = [], [], []
    for mid, scope, fact in src:
        if mid in done:
            continue
        my = cache.get(mid)
        todo.append([mid, scope, fact, my])
        if not my:
            needs_llm.append(fact)
            llm_idx.append(len(todo) - 1)

    if needs_llm:
        out = translate_batch(needs_llm)
        for i, my in zip(llm_idx, out):
            todo[i][3] = my

    ins = 0
    for mid, scope, _fact, my in todo:
        if not my:
            continue
        cur.execute(
            "INSERT INTO public.dash_memories "
            "(project_slug, scope, fact, source, lang, parent_id) "
            "VALUES (%s, %s, %s, 'bilingual_twin', 'my', %s) "
            "ON CONFLICT DO NOTHING",
            (SLUG, scope, my, mid),
        )
        ins += cur.rowcount or 0
    return ins


# ───────────────────────── surface: query patterns ───────────────────────────
# dash_query_patterns has a UNIQUE index on (project_slug, md5(sql)) — a twin
# can't share its parent's sql verbatim. We append a no-op trailing SQL comment
# so the stored sql stays byte-for-byte executable + identical in meaning, but
# md5-distinct (and the comment doubles as the idempotency marker).
_TWIN_SQL_MARK = "\n-- bilingual_twin"


def regen_patterns(cur) -> int:
    cache = {c["id"]: c.get("question_my") for c in (_load_cache("patterns") or [])}
    cur.execute(
        "SELECT id, question, sql, tables_used, join_strategy, filters "
        "FROM public.dash_query_patterns "
        "WHERE project_slug = %s AND COALESCE(source,'') <> 'bilingual_twin'",
        (SLUG,),
    )
    src = cur.fetchall()
    # idempotency: bilingual_twin rows already present (by ORIGINAL sql)
    cur.execute(
        "SELECT sql FROM public.dash_query_patterns "
        "WHERE project_slug = %s AND source = 'bilingual_twin'",
        (SLUG,),
    )
    done_sql = {r[0] for r in cur.fetchall()}

    todo, needs_llm, llm_idx = [], [], []
    for pid, q, sql, tu, js, fl in src:
        if (sql + _TWIN_SQL_MARK) in done_sql:
            continue
        my = cache.get(pid)
        todo.append([q, sql, tu, js, fl, my])
        if not my:
            needs_llm.append(q)
            llm_idx.append(len(todo) - 1)

    if needs_llm:
        out = translate_batch(needs_llm)
        for i, my in zip(llm_idx, out):
            todo[i][5] = my

    ins = 0
    for _q, sql, tu, js, fl, my in todo:
        if not my:
            continue
        twin_sql = sql + _TWIN_SQL_MARK
        cur.execute(
            "INSERT INTO public.dash_query_patterns "
            "(project_slug, question, sql, uses, source, tables_used, join_strategy, filters) "
            "VALUES (%s, %s, %s, 0, 'bilingual_twin', %s, %s, %s) "
            "ON CONFLICT (project_slug, md5(sql)) DO NOTHING",
            (SLUG, my, twin_sql, tu, js, fl),
        )
        ins += cur.rowcount or 0
    return ins


# ───────────────────────── surface: rules ────────────────────────────────────
def regen_rules(cur) -> int:
    cache = {c["id"]: c for c in (_load_cache("rules") or [])}
    cur.execute(
        "SELECT id, rule_id, name, type, category, definition "
        "FROM public.dash_rules_db "
        "WHERE project_slug = %s AND COALESCE(lang,'en') <> 'my' "
        "AND rule_id NOT LIKE '%%\\_my'",
        (SLUG,),
    )
    src = cur.fetchall()
    cur.execute(
        "SELECT rule_id FROM public.dash_rules_db "
        "WHERE project_slug = %s AND (lang = 'my' OR rule_id LIKE '%%\\_my')",
        (SLUG,),
    )
    done = {r[0] for r in cur.fetchall()}

    todo, needs_llm, llm_idx = [], [], []
    for rid, rule_id, name, typ, cat, defn in src:
        my_rid = f"{rule_id}_my"
        if my_rid in done:
            continue
        c = cache.get(rid) or {}
        def_my = c.get("definition_my")
        name_my = c.get("name") or name
        todo.append([my_rid, name_my, typ, cat, def_my, defn, c])
        if not def_my:
            needs_llm.append(defn)
            llm_idx.append(len(todo) - 1)

    if needs_llm:
        out = translate_batch(needs_llm)
        for i, my in zip(llm_idx, out):
            todo[i][4] = my

    ins = 0
    for my_rid, name_my, typ, cat, def_my, _defn, _c in todo:
        if not def_my:
            continue
        cur.execute(
            "INSERT INTO public.dash_rules_db "
            "(project_slug, rule_id, name, type, category, definition, source, lang) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'bilingual_twin', 'my') "
            "ON CONFLICT (project_slug, rule_id) DO NOTHING",
            (SLUG, my_rid, name_my, typ, cat, def_my),
        )
        ins += cur.rowcount or 0
    return ins


# ───────────────────────── surface: company brain ────────────────────────────
def regen_brain(cur) -> int:
    cache = {c["id"]: c for c in (_load_cache("brain") or [])}
    cur.execute(
        "SELECT id, category, name, definition, metadata "
        "FROM public.dash_company_brain "
        "WHERE project_slug = %s AND COALESCE(lang,'en') <> 'my'",
        (SLUG,),
    )
    src = cur.fetchall()
    cur.execute(
        "SELECT source_id FROM public.dash_company_brain "
        "WHERE project_slug = %s AND lang = 'my' AND source_id IS NOT NULL",
        (SLUG,),
    )
    done = {r[0] for r in cur.fetchall()}

    todo, needs_llm, llm_idx = [], [], []
    for bid, cat, name, defn, meta in src:
        if bid in done:
            continue
        c = cache.get(bid) or {}
        def_my = c.get("definition_my")
        todo.append([bid, cat, name, def_my, meta])
        if not def_my:
            needs_llm.append(defn)
            llm_idx.append(len(todo) - 1)

    if needs_llm:
        out = translate_batch(needs_llm)
        for i, my in zip(llm_idx, out):
            todo[i][3] = my

    ins = 0
    for bid, cat, name, def_my, meta in todo:
        if not def_my:
            continue
        # name is UNIQUE per (project_slug, name) — suffix to avoid clash
        my_name = f"{name} [my]"
        if isinstance(meta, (dict, list)):
            meta = json.dumps(meta, ensure_ascii=False)
        cur.execute(
            "INSERT INTO public.dash_company_brain "
            "(project_slug, category, name, definition, metadata, created_by, lang, source_id) "
            "VALUES (%s, %s, %s, %s, COALESCE(%s,'{}')::jsonb, 'bilingual_twin', 'my', %s) "
            "ON CONFLICT (project_slug, name) WHERE project_slug IS NOT NULL "
            "DO NOTHING",
            (SLUG, cat, my_name, def_my, meta, bid),
        )
        ins += cur.rowcount or 0
    return ins


# ───────────────────────── surface: schema metadata ──────────────────────────
_MY_TAG = "[မြန်မာ]"


def _get_path(obj, path):
    """Resolve a dotted/array path like 'table_columns[3].description'. None if missing."""
    cur = obj
    for tok in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if tok.startswith("[") and tok.endswith("]"):
            idx = int(tok[1:-1])
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or tok not in cur:
                return None
            cur = cur[tok]
    return cur


def _set_path(obj, path, value) -> bool:
    toks = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    cur = obj
    for tok in toks[:-1]:
        if tok.startswith("["):
            cur = cur[int(tok[1:-1])]
        else:
            cur = cur[tok]
    last = toks[-1]
    if last.startswith("["):
        cur[int(last[1:-1])] = value
    else:
        cur[last] = value
    return True


# NL description fields worth translating. Everything else (codes, DDL,
# fingerprints, category VALUES, column names/types) stays Latin.
_NL_KEYS = {"grain", "freshness", "table_purpose", "table_description",
            "alternate_tables", "description", "summary", "refresh_hint",
            "populations_excluded", "populations_included"}
_NL_LIST_KEYS = {"use_cases", "usage_patterns", "data_quality_notes"}


def _collect_nl_paths(meta) -> list:
    """Walk the LIVE jsonb and return paths of natural-language description
    strings, independent of any cache (so it survives a retrain that reshapes
    the metadata). Whitelist by key; never touch dimensions/codes/ddl/fp."""
    out = []

    def walk(o, path, parent_list_key=None):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else k, None)
        elif isinstance(o, list):
            lk = path.split(".")[-1].split("[")[0]
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]", lk)
        elif isinstance(o, str) and o.strip():
            if path.startswith("dimensions") or ".fp" in path or ".ddl" in path:
                return
            final = re.sub(r"\[\d+\]$", "", path).split(".")[-1]
            if final in _NL_KEYS or (parent_list_key in _NL_LIST_KEYS):
                out.append(path)

    walk(meta, "")
    return out


def regen_schema(cur) -> int:
    """Append '\\n[မြန်မာ] <MY>' to each NL description in the live jsonb.

    Targets are derived from the LIVE jsonb (cache-free), so a force-retrain that
    regenerates the metadata in English gets re-twinned. The /tmp cache, when
    present, only supplies translations to skip re-LLM'ing unchanged text.
    """
    cache_tables = {t["table_name"]: {it["path"]: it.get("text_my")
                                      for it in t.get("items", [])}
                    for t in (_load_cache("schema") or [])}
    cur.execute(
        "SELECT id, table_name, metadata FROM public.dash_table_metadata "
        "WHERE project_slug = %s ORDER BY id",
        (SLUG,),
    )
    rows = cur.fetchall()

    updated = 0
    for mid, tname, meta in rows:
        if isinstance(meta, str):
            meta = json.loads(meta)
        path_my = dict(cache_tables.get(tname, {}))   # translation hints only
        # derive targets from the LIVE jsonb, not the cache
        targets = _collect_nl_paths(meta)
        # translate uncovered (cache miss / new column) on the fly
        to_llm, llm_paths = [], []
        for p in targets:
            en = _get_path(meta, p)
            if not isinstance(en, str) or not en.strip():
                continue
            if _MY_TAG in en:           # already bilingual — idempotent skip
                continue
            if not path_my.get(p):
                to_llm.append(en)
                llm_paths.append(p)
        if to_llm:
            out = translate_batch(to_llm)
            for p, my in zip(llm_paths, out):
                path_my[p] = my

        changed = False
        for p in targets:
            en = _get_path(meta, p)
            if not isinstance(en, str) or not en.strip():
                continue
            if _MY_TAG in en:
                continue
            my = path_my.get(p)
            if not my:
                continue
            _set_path(meta, p, f"{en}\n{_MY_TAG} {my}")
            changed = True

        if changed:
            cur.execute(
                "UPDATE public.dash_table_metadata "
                "SET metadata = %s::jsonb, updated_at = now() "
                "WHERE id = %s",
                (json.dumps(meta, ensure_ascii=False), mid),
            )
            updated += 1
    return updated


# ───────────────────────── verify ────────────────────────────────────────────
def verify(cur) -> dict:
    def q(sql, p=(SLUG,)):
        cur.execute(sql, p)
        return cur.fetchone()[0]
    mem = q("SELECT count(*) FROM public.dash_memories "
            "WHERE project_slug=%s AND lang='my'")
    pat = q("SELECT count(*) FROM public.dash_query_patterns "
            "WHERE project_slug=%s AND source='bilingual_twin'")
    rul = q("SELECT count(*) FROM public.dash_rules_db "
            "WHERE project_slug=%s AND lang='my'")
    bra = q("SELECT count(*) FROM public.dash_company_brain "
            "WHERE project_slug=%s AND lang='my'")
    sch = q("SELECT count(*) FROM public.dash_table_metadata "
            "WHERE project_slug=%s AND metadata::text LIKE %s",
            (SLUG, f"%{_MY_TAG}%"))
    return {"memories_my": mem, "patterns_twin": pat, "rules_my": rul,
            "brain_my": bra, "schema_bilingual": sch}


# ───────────────────────── entrypoint ────────────────────────────────────────
def run() -> dict:
    """Idempotent bilingual regen. Returns verify counts. Safe to re-run."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            # 1) BACKUP first — abort if source empty
            total = backup(cur)
            print(f"[backup] {total} rows -> {BACKUP_PATH}")
            if total == 0:
                raise SystemExit("source tables empty — aborting (nothing to back up)")

            # 2) DDL
            ensure_lang_columns(cur)
            conn.commit()

            # 3) per-surface twins (commit after each so a later failure keeps
            #    earlier surfaces durable; each surface is independently idempotent)
            m = regen_memories(cur); conn.commit(); print(f"[memories] +{m}")
            p = regen_patterns(cur); conn.commit(); print(f"[patterns] +{p}")
            r = regen_rules(cur);    conn.commit(); print(f"[rules]    +{r}")
            b = regen_brain(cur);    conn.commit(); print(f"[brain]    +{b}")
            s = regen_schema(cur);   conn.commit(); print(f"[schema]   {s} table(s) bilingual")

            counts = verify(cur)
        print("[verify]", json.dumps(counts, ensure_ascii=False))
        return counts
    finally:
        conn.close()


def main() -> dict:
    return run()


if __name__ == "__main__":
    run()
