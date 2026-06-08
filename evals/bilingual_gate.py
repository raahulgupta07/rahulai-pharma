#!/usr/bin/env python3
"""Phase 3 — Burmese bilingual eval gate.

Measures the REAL deployed behaviour (hits the gateway HTTP path, so REPLY_LANG +
training-twin retrieval both fire) and quantifies the run-to-run VARIANCE that
prompt/training fixes alone can't kill. Each case runs N times; we score every run
for (1) Burmese-ness (deterministic %Burmese of the prose) and (2) correctness
(known-number substring for analytical cases; LLM judge for free-text indication).

Outputs a per-case + overall pass-rate and a variance band, then exits non-zero if
overall pass-rate < --min-pass (so it doubles as a CI / release gate).

Run on the host (needs gateway on :8011 + the fixtures key file):
    python evals/bilingual_gate.py --runs 3 --min-pass 70
"""
import os, re, json, time, argparse, statistics, urllib.request

BASE = os.environ.get("CP_BASE", "http://127.0.0.1:8011")
URL = f"{BASE}/api/v1/chat/completions"
HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "..", "examples", "outlet-test", "fixtures.json")
JUDGE_MODEL = "google/gemini-3-flash-preview"

# Burmese-pass threshold: ≥40% of alpha chars Burmese ⇒ "answered in Burmese"
# (the rest is unavoidable English brand names + Arabic digits).
MY_THRESHOLD = 40.0


def _or_key():
    # read OpenRouter key from .env for the judge
    env = os.path.join(HERE, "..", ".env")
    for ln in open(env, encoding="utf-8"):
        if ln.startswith("OPENROUTER_API_KEYS=") or ln.startswith("OPENROUTER_API_KEY="):
            return ln.split("=", 1)[1].split(",")[0].strip()
    return ""


def my_ratio(c: str) -> float:
    L = [ch for ch in c if ch.isalpha()]
    M = [ch for ch in L if 'က' <= ch <= '႟']
    return (len(M) / len(L) * 100) if L else 0.0


def ask(key: str, q: str, sid: str) -> tuple[str, float]:
    # Cache-bust: the gateway caches on normalized question text, so repeated runs
    # would return the first answer (0s) and hide the real run-to-run variance we
    # are trying to measure. Append a unique invisible-ish ref the agent ignores.
    q = q + f"​(r:{sid})"
    body = json.dumps({"model": "citypharma", "messages": [{"role": "user", "content": q}],
                       "stream": False, "session_id": sid, "reasoning": "quick"}).encode()
    r = urllib.request.Request(URL, data=body, method="POST",
                               headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t = time.time()
    j = json.loads(urllib.request.urlopen(r, timeout=240).read())
    return j["choices"][0]["message"]["content"], time.time() - t


def judge_correct(question: str, answer: str, expect: str) -> bool:
    """LLM judge: does the answer address the question per `expect`? Binary."""
    prompt = (f"Question: {question}\nExpected gist: {expect}\nAnswer: {answer[:800]}\n\n"
              "Does the answer correctly address the question per the expected gist? "
              "Ignore language/formatting; judge substance only. Reply ONLY 'YES' or 'NO'.")
    body = json.dumps({"model": JUDGE_MODEL, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0}).encode()
    r = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
                               headers={"Authorization": f"Bearer {_or_key()}", "Content-Type": "application/json"})
    txt = json.loads(urllib.request.urlopen(r, timeout=60).read())["choices"][0]["message"]["content"]
    return "YES" in txt.upper()


def build_cases(fx):
    gk = fx["global_key"]
    sk = fx["keys"][sorted(fx["keys"])[0]]
    B = fx["brands"][0]["brand"]
    # number = deterministic substring check; gist = LLM judge
    return [
        {"id": "active-count", "key": gk, "number": "4,649",
         "q": "catalog ထဲမှာ active ဖြစ်နေတဲ့ product ဘယ်နှစ်ခု ရှိလဲ။"},
        {"id": "total-stock", "key": gk, "number": None, "gist": "total stock quantity across all records (a large number)",
         "q": "စုစုပေါင်း stock အရေအတွက် ဘယ်လောက်ရှိလဲ။"},
        {"id": "indication", "key": sk, "number": None, "gist": f"what {B} is used for (skincare/antioxidant)",
         "q": f"{B} ဆေးက ဘာအတွက် သုံးတာလဲ။"},
        {"id": "categories", "key": gk, "number": None, "gist": "product categories with most unique generic names",
         "q": "ဘယ် product category တွေမှာ generic name အများဆုံး ရှိလဲ။"},
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=int(os.environ.get("CP_RUNS", "3")))
    ap.add_argument("--min-pass", type=float, default=70.0)
    args = ap.parse_args()

    fx = json.load(open(os.path.abspath(FIX), encoding="utf-8"))
    cases = build_cases(fx)
    print(f"Bilingual gate — {len(cases)} cases × {args.runs} runs (gateway {BASE})\n")

    all_runs = []
    for c in cases:
        my_pcts, passes, lats = [], 0, []
        for k in range(args.runs):
            try:
                ans, lat = ask(c["key"], c["q"], f"gate-{c['id']}-{k}")
            except Exception as e:
                print(f"  {c['id']} run{k+1}: ERROR {str(e)[:50]}")
                my_pcts.append(0.0); all_runs.append(False); continue
            r = my_ratio(ans)
            is_my = r >= MY_THRESHOLD
            if c.get("number"):
                ok = c["number"].replace(",", "") in ans.replace(",", "")
            else:
                ok = judge_correct(c["q"], ans, c.get("gist", ""))
            passed = is_my and ok
            my_pcts.append(r); lats.append(lat); passes += int(passed); all_runs.append(passed)
            print(f"  {c['id']:14} run{k+1}: {r:4.0f}%MY  correct={ok}  -> {'PASS' if passed else 'FAIL'}  {lat:.0f}s")
        band = f"{min(my_pcts):.0f}-{max(my_pcts):.0f}%" if my_pcts else "-"
        print(f"  └ {c['id']}: {passes}/{args.runs} pass · %MY band {band} · "
              f"σ={statistics.pstdev(my_pcts):.0f}\n")

    rate = 100 * sum(all_runs) / len(all_runs) if all_runs else 0
    verdict = "PASS" if rate >= args.min_pass else "FAIL"
    print("=" * 48)
    print(f"BILINGUAL GATE: pass_rate={rate:.0f}%  threshold={args.min_pass:.0f}%  -> {verdict}")
    raise SystemExit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
