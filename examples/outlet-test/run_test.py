#!/usr/bin/env python3
"""Bilingual per-outlet API accuracy + latency test for the CityPharma gateway.

For every outlet (store-scoped service key) it fires the same indication
question in English and Burmese, then scores the reply for correctness
(keyword overlap vs the catalog `indication`), reply-language, latency and
token usage. Output: per-row CSV + an aggregate summary printed to stdout.
"""
import json, time, re, csv, statistics, concurrent.futures, urllib.request, urllib.error, os

BASE = os.environ.get("CP_BASE", "http://127.0.0.1:8011")
URL = f"{BASE}/api/v1/chat/completions"
HERE = os.path.dirname(os.path.abspath(__file__))
FX = json.load(open(os.path.join(HERE, "fixtures.json"), encoding="utf-8"))
CONCURRENCY = int(os.environ.get("CP_CONC", "6"))
TIMEOUT = 90

KEYS = FX["keys"]                       # {site_code: api_key}
BRANDS = FX["brands"]                   # [{brand, generic, indication}]
OUTLETS = sorted(KEYS.keys())
LIMIT = int(os.environ.get("CP_LIMIT", "0"))
if LIMIT:                                # evenly-spaced sample across all outlets
    step = max(1, len(OUTLETS) // LIMIT)
    OUTLETS = OUTLETS[::step][:LIMIT]
ONLY = os.environ.get("CP_ONLY", "").strip()
if ONLY:                                 # one-outlet mode, keep original brand index
    _all = sorted(KEYS.keys())
    OUTLETS = [ONLY]
    _BRAND_IDX = {ONLY: _all.index(ONLY)}
else:
    _BRAND_IDX = {o: i for i, o in enumerate(OUTLETS)}

STOP = set(("the a an of for and or to in on with is are be used use treat treatment help "
            "helps support supports may relief reduce reduces health daily this that from "
            "your you it its as well being supplement").split())


def burmese(s: str) -> bool:
    return any('က' <= ch <= '႟' for ch in s)


def kw(text: str):
    return [w for w in re.findall(r"[a-zA-Z]{4,}", (text or "").lower()) if w not in STOP]


def ask(key: str, question: str):
    body = json.dumps({
        "model": "citypharma",
        "messages": [{"role": "user", "content": question}],
        "stream": False,
    }).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            j = json.loads(r.read())
        dt = (time.time() - t0) * 1000
        ans = (j.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
        usage = j.get("usage", {}) or {}
        return {"ms": dt, "status": 200, "answer": ans,
                "tok_in": usage.get("prompt_tokens"), "tok_out": usage.get("completion_tokens")}
    except urllib.error.HTTPError as e:
        return {"ms": (time.time() - t0) * 1000, "status": e.code,
                "answer": e.read().decode()[:300], "tok_in": None, "tok_out": None}
    except Exception as e:
        return {"ms": (time.time() - t0) * 1000, "status": -1,
                "answer": f"{type(e).__name__}: {e}", "tok_in": None, "tok_out": None}


def task(idx, outlet):
    brand = BRANDS[_BRAND_IDX[outlet] % len(BRANDS)]
    gen = (brand.get("generic") or "").strip()
    truth = kw(brand["indication"]) + ([gen.lower()] if gen else [])
    key = KEYS[outlet]
    rows = []
    qs = [
        ("EN", f"What is {brand['brand']} used for?"),
        ("MY", f"{brand['brand']} ဆေးက ဘာအတွက် သုံးတာလဲ။"),
    ]
    for lang, q in qs:
        r = ask(key, q)
        ans = r["answer"]
        al = ans.lower()
        hits = sum(1 for w in set(truth) if w and w in al)
        ok = r["status"] == 200 and hits >= 2
        reply_lang = "MY" if burmese(ans) else ("EN" if ans.strip() else "-")
        lang_ok = (reply_lang == lang) if r["status"] == 200 else False
        rows.append({
            "outlet": outlet, "lang": lang, "brand": brand["brand"], "q": q,
            "status": r["status"], "ms": round(r["ms"]),
            "tok_in": r["tok_in"], "tok_out": r["tok_out"],
            "kw_hits": hits, "accurate": ok, "reply_lang": reply_lang, "lang_ok": lang_ok,
            "answer": ans.replace("\n", " ")[:300],
        })
    return rows


def main():
    print(f"Outlets: {len(OUTLETS)}  langs: EN+MY  calls: {len(OUTLETS)*2}  conc: {CONCURRENCY}\n")
    t0 = time.time()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(task, i, o): o for i, o in enumerate(OUTLETS)}
        done = 0
        for f in concurrent.futures.as_completed(futs):
            results.extend(f.result())
            done += 1
            if done % 5 == 0 or done == len(OUTLETS):
                print(f"  {done}/{len(OUTLETS)} outlets done  ({round(time.time()-t0)}s)")
    wall = time.time() - t0
    results.sort(key=lambda r: (r["outlet"], r["lang"]))

    csv_path = os.path.join(HERE, "results.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
        w.writeheader(); w.writerows(results)
    # cumulative master across approval-gated runs
    master = os.path.join(HERE, "results_all.csv")
    new = not os.path.exists(master)
    with open(master, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
        if new:
            w.writeheader()
        w.writerows(results)

    def agg(rows):
        n = len(rows)
        ok2 = [r for r in rows if r["status"] == 200]
        lat = [r["ms"] for r in ok2] or [0]
        acc = sum(1 for r in rows if r["accurate"])
        lang = sum(1 for r in rows if r["lang_ok"])
        tok = [r["tok_out"] for r in ok2 if r["tok_out"]]
        return {
            "n": n, "http_ok": len(ok2), "accuracy": f"{acc}/{n} ({round(100*acc/n)}%)",
            "lang_match": f"{lang}/{n} ({round(100*lang/n)}%)",
            "p50_ms": round(statistics.median(lat)), "p95_ms": round(sorted(lat)[int(len(lat)*0.95)-1]),
            "max_ms": max(lat), "avg_tok_out": round(statistics.mean(tok)) if tok else 0,
        }

    EN = [r for r in results if r["lang"] == "EN"]
    MY = [r for r in results if r["lang"] == "MY"]
    print("\n================ SUMMARY ================")
    print(f"wall: {round(wall)}s  total calls: {len(results)}  CSV: {csv_path}\n")
    for label, rows in (("ALL", results), ("EN", EN), ("MY", MY)):
        a = agg(rows)
        print(f"[{label:3}] acc {a['accuracy']:>14}  lang {a['lang_match']:>13}  "
              f"http_ok {a['http_ok']}/{a['n']}  p50 {a['p50_ms']}ms  p95 {a['p95_ms']}ms  "
              f"max {a['max_ms']}ms  avg_out_tok {a['avg_tok_out']}")
    bad = [r for r in results if r["status"] != 200]
    if bad:
        print(f"\nNON-200 ({len(bad)}):")
        for r in bad[:10]:
            print(f"  {r['outlet']} {r['lang']} -> {r['status']}: {r['answer'][:120]}")
    print("\nSample answers:")
    for r in results[:4]:
        print(f"  [{r['outlet']} {r['lang']} acc={r['accurate']} {r['ms']}ms] {r['answer'][:160]}")


if __name__ == "__main__":
    main()
