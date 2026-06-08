#!/usr/bin/env python3
"""Fire N stores at the EMBED endpoint in PARALLEL and measure each reply.

Per store, the 2-step embed flow:
  1) POST /api/embed/session/create  {embed_id, public_key}   (Origin header required)
     -> {session_token}
  2) POST /api/embed/chat            {session_token, message}
     -> {content, latency_ms, cache_hit}
All stores run concurrently in a thread pool. Reality check: the embed path is
gated by an async semaphore (LLM_PARALLEL_CAP_CHAT, default 10) — to get all 20
truly parallel, set LLM_PARALLEL_CAP_CHAT=20 in .env + recreate cp-api.

Usage (host):
  python3 examples/embed-test/run_embed_test.py            # EN + MY, all stores
  CONCURRENCY=20 LANGS=en,my python3 .../run_embed_test.py
Env: BASE (default http://127.0.0.1:8011), CONCURRENCY (default 20),
     LANGS (csv en,my), FIXTURES (embed_fixtures.json path).
Writes results CSV next to the fixtures.
"""
import os, json, time, csv, urllib.request, urllib.error, concurrent.futures as cf

BASE = os.getenv("BASE", "http://127.0.0.1:8011")
HERE = os.path.dirname(os.path.abspath(__file__))
FIX = json.load(open(os.getenv("FIXTURES", os.path.join(HERE, "embed_fixtures.json"))))
ORIGIN = FIX.get("origin", "http://localhost:3000")
EMBEDS = FIX["embeds"]                       # {store: {embed_id, public_key}}
CONC = int(os.getenv("CONCURRENCY", "20"))
LANGS = (os.getenv("LANGS", "en,my")).split(",")

# store-scoped questions (the embed binds the answer to THIS store's stock)
Q = {
    "en": "What is the total stock quantity at my outlet? Give the number.",
    "my": "ကျွန်တော့်ဆိုင်မှာ စုစုပေါင်း လက်ကျန်ပစ္စည်း အရေအတွက် ဘယ်လောက်ရှိလဲ။ ဂဏန်းနဲ့ဖြေပါ။",
}


def _post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Origin": ORIGIN})
    with urllib.request.urlopen(req, timeout=240) as r:
        return r.status, json.loads(r.read())


def _pct_my(s):
    nonsp = [c for c in s if not c.isspace()]
    my = sum('က' <= c <= '႟' for c in s)
    return round(100 * my / max(1, len(nonsp)))


def one(store, meta, lang):
    eid, pk = meta["embed_id"], meta["public_key"]
    t0 = time.time()
    try:
        st, sess = _post("/api/embed/session/create",
                         {"embed_id": eid, "public_key": pk})
        tok = sess.get("session_token")
        if not tok:
            return dict(store=store, lang=lang, ok=False, err=f"session:{sess}", secs=0)
        st, rep = _post("/api/embed/chat", {"session_token": tok, "message": Q[lang]})
        secs = round(time.time() - t0, 1)
        content = rep.get("content", "") if isinstance(rep, dict) else str(rep)
        return dict(store=store, lang=lang, ok=bool(content), secs=secs,
                    my=_pct_my(content), cache=rep.get("cache_hit"),
                    chars=len(content), answer=content[:160].replace("\n", " "), err="")
    except urllib.error.HTTPError as e:
        return dict(store=store, lang=lang, ok=False, secs=round(time.time()-t0, 1),
                    err=f"HTTP {e.code}: {e.read()[:120]}")
    except Exception as e:
        return dict(store=store, lang=lang, ok=False, secs=round(time.time()-t0, 1),
                    err=str(e)[:120])


def main():
    jobs = [(s, m, lang) for s, m in EMBEDS.items() for lang in LANGS]
    print(f"{len(jobs)} requests ({len(EMBEDS)} stores × {len(LANGS)} langs), "
          f"concurrency={CONC}, base={BASE}\n")
    wall0 = time.time()
    rows = []
    with cf.ThreadPoolExecutor(max_workers=CONC) as ex:
        futs = [ex.submit(one, s, m, lang) for s, m, lang in jobs]
        for f in cf.as_completed(futs):
            r = f.result()
            rows.append(r)
            tag = "OK " if r["ok"] else "ERR"
            print(f"[{tag}] {r['store']:<14} {r['lang']}  {r.get('secs',0):>5}s  "
                  f"MY={r.get('my','-')}%  {r.get('err') or r.get('answer','')[:80]}")
    wall = round(time.time() - wall0, 1)

    ok = [r for r in rows if r["ok"]]
    print(f"\n── summary ── {len(ok)}/{len(rows)} ok · wall {wall}s")
    if ok:
        secs = sorted(r["secs"] for r in ok)
        print(f"latency  min {secs[0]}s · median {secs[len(secs)//2]}s · max {secs[-1]}s")
        for lang in LANGS:
            lr = [r for r in ok if r["lang"] == lang]
            if lr:
                print(f"  {lang}: avg MY {round(sum(r['my'] for r in lr)/len(lr))}% · "
                      f"cache_hits {sum(1 for r in lr if r.get('cache'))}/{len(lr)}")
    out = os.path.join(HERE, "embed_results.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["store", "lang", "ok", "secs", "my",
                                          "cache", "chars", "answer", "err"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
