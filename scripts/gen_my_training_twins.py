#!/usr/bin/env python3
"""Phase 2 — generate Burmese twins of the English training Q&A pairs.

For every English training question (in knowledge/<slug>/training/*_qa.json), add
a Burmese-translated twin with the SAME sql / verified answer / metadata. Twins go
into both the JSON files (drive lexical retrieval) and public.dash_training_qa (the
DB mirror). The SQL is never touched — only the question text is translated.

Idempotent: an entry with lang=='my' (or a question already Burmese) is skipped, so
re-running won't duplicate. Run inside the cp-api container (OpenRouter key + DB).
"""
import os, re, json, glob, urllib.request

SLUG = "citypharma"
TRAIN_DIR = f"/app/knowledge/{SLUG}/training"
MODEL = "google/gemini-3-flash-preview"


def _key():
    k = os.getenv("OPENROUTER_API_KEYS") or os.getenv("OPENROUTER_API_KEY") or ""
    return k.split(",")[0].strip()


def _is_my(s: str) -> bool:
    return any('က' <= c <= '႟' for c in (s or ""))


def translate_batch(questions):
    """One LLM call: English questions -> Burmese. Returns list same length/order."""
    numbered = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
    prompt = (
        "Translate each numbered pharmacy data question into natural, fluent Burmese "
        "(မြန်မာ). Keep database/column identifiers, brand names, and numbers in their "
        "original Latin/Arabic form — translate only the natural-language words. Return "
        "ONLY a JSON array of strings, same order, no numbering, no extra text.\n\n"
        + numbered
    )
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": "Bearer " + _key(), "Content-Type": "application/json"})
    raw = json.loads(urllib.request.urlopen(req, timeout=120).read())
    txt = raw["choices"][0]["message"]["content"]
    m = re.search(r"\[.*\]", txt, re.DOTALL)
    arr = json.loads(m.group(0))
    if len(arr) != len(questions):
        raise SystemExit(f"translation count mismatch: {len(arr)} vs {len(questions)}")
    return arr


def main():
    files = sorted(glob.glob(f"{TRAIN_DIR}/*_qa.json"))
    print("QA files:", files)

    # 1) collect all English originals needing a twin (across files)
    to_translate, index = [], []   # index: (file, entry_dict)
    file_data = {}
    for f in files:
        data = json.load(open(f, encoding="utf-8"))
        file_data[f] = data
        has_my = any(e.get("lang") == "my" or _is_my(e.get("question", "")) for e in data)
        for e in data:
            if e.get("lang") == "my" or _is_my(e.get("question", "")):
                continue
            if has_my:   # this file already has twins from a prior run for some Qs
                # still skip only the ones that already have a matching my twin
                if any(t.get("lang") == "my" and t.get("src_en") == e.get("question") for t in data):
                    continue
            to_translate.append(e["question"])
            index.append((f, e))

    if not to_translate:
        print("Nothing to translate — twins already present. Idempotent no-op.")
        return
    print(f"Translating {len(to_translate)} questions -> Burmese …")
    my = translate_batch(to_translate)

    # 2) append twins to each file's list
    new_per_file = {}
    for (f, e), myq in zip(index, my):
        twin = dict(e)
        twin["question"] = myq
        twin["lang"] = "my"
        twin["src_en"] = e["question"]
        file_data[f].append(twin)
        new_per_file.setdefault(f, []).append(twin)

    for f, data in file_data.items():
        json.dump(data, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  {os.path.basename(f)}: +{len(new_per_file.get(f, []))} twins (now {len(data)})")

    # 3) mirror twins into the DB table
    from sqlalchemy import text
    from app.auth import _engine
    ins = 0
    with _engine.begin() as c:
        for f, twins in new_per_file.items():
            tname = os.path.basename(f).replace("_qa.json", "")
            for t in twins:
                c.execute(text(
                    "INSERT INTO public.dash_training_qa "
                    "(project_slug, table_name, question, sql, answer_template) "
                    "VALUES (:p,:t,:q,:s,:a)"),
                    {"p": SLUG, "t": tname, "q": t["question"], "s": t.get("sql", ""),
                     "a": t.get("verified_answer", "")})
                ins += 1
    print(f"DB: inserted {ins} Burmese twins into dash_training_qa")
    print("DONE.")


if __name__ == "__main__":
    main()
