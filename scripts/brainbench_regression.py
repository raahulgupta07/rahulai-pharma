#!/usr/bin/env python3
"""
brainbench_regression.py — Python regression gate w/ rich table + JUnit XML +
optional Slack notification on regression.

Same gate as the bash variant:
  * pulls top-N highest-judge captured sessions across all projects from
    dash.dash_brainbench_corpus
  * POSTs to /api/projects/{slug}/brainbench/runs per project
  * polls until done, fetches summary
  * exits non-zero iff regressions > 0 AND avg_score_delta < FAIL_DELTA

Env:
    DASH_API_URL         base URL (default http://localhost:8000)
    DASH_API_TOKEN       bearer token (required)
    DASH_DB_URL          SQLAlchemy URL OR psycopg DSN (required)
    TOP_N                default 10
    POLL_TIMEOUT         seconds per run, default 900
    FAIL_DELTA           default -0.3
    SLACK_WEBHOOK_URL    optional — posts on regression
    JUNIT_XML            output path (default brainbench-junit.xml)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from typing import Any
from xml.sax.saxutils import escape

API_URL = os.environ.get("DASH_API_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("DASH_API_TOKEN", "")
DB_URL = os.environ.get("DASH_DB_URL", "")
TOP_N = int(os.environ.get("TOP_N", "10"))
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "900"))
FAIL_DELTA = float(os.environ.get("FAIL_DELTA", "-0.3"))
SLACK = os.environ.get("SLACK_WEBHOOK_URL", "")
JUNIT = os.environ.get("JUNIT_XML", "brainbench-junit.xml")


def _http(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{API_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code} {url}: {msg}")


def _fetch_corpus() -> list[tuple[str, int, float]]:
    """Returns [(project_slug, corpus_id, judge_score), ...]"""
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool
        eng = create_engine(DB_URL, poolclass=NullPool)
        sql = text(
            """
            SELECT project_slug, id, original_judge_score
              FROM dash.dash_brainbench_corpus
             WHERE original_judge_score IS NOT NULL
             ORDER BY original_judge_score DESC, created_at DESC
             LIMIT :n
            """
        )
        with eng.connect() as c:
            return [(r[0], int(r[1]), float(r[2])) for r in c.execute(sql, {"n": TOP_N})]
    except Exception as e:
        print(f"[fatal] corpus fetch failed: {e}", file=sys.stderr)
        sys.exit(3)


def _slack(text: str) -> None:
    if not SLACK:
        return
    try:
        req = urllib.request.Request(
            SLACK, data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"[warn] slack post failed: {e}", file=sys.stderr)


def _print_table(rows: list[dict]) -> None:
    cols = ["project", "run_id", "total", "wins", "regr", "ties", "errs", "avg_Δ", "status"]
    widths = {c: max(len(c), max((len(str(r.get(c, "-"))) for r in rows), default=0)) for c in cols}
    sep = "  ".join("─" * widths[c] for c in cols)
    head = "  ".join(c.ljust(widths[c]) for c in cols)
    print(head)
    print(sep)
    for r in rows:
        print("  ".join(str(r.get(c, "-")).ljust(widths[c]) for c in cols))


def _write_junit(rows: list[dict], failed: bool, reason: str) -> None:
    cases = []
    for r in rows:
        name = escape(f"brainbench/{r['project']}/run_{r['run_id']}")
        if r.get("status") == "done" and (r.get("regr", 0) or 0) == 0:
            cases.append(f'<testcase classname="brainbench" name="{name}" time="0"/>')
        else:
            msg = escape(f"regressions={r.get('regr')} avg_Δ={r.get('avg_Δ')} status={r.get('status')}")
            cases.append(
                f'<testcase classname="brainbench" name="{name}" time="0">'
                f'<failure message="{msg}"/></testcase>'
            )
    suite_attrs = f'name="brainbench-regression" tests="{len(rows)}" failures="{1 if failed else 0}"'
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuite {suite_attrs}>\n'
        + "\n".join(cases)
        + (f'\n<system-out>{escape(reason)}</system-out>\n' if reason else "\n")
        + '</testsuite>\n'
    )
    try:
        with open(JUNIT, "w") as f:
            f.write(xml)
    except Exception as e:
        print(f"[warn] junit write failed: {e}", file=sys.stderr)


def main() -> int:
    if not TOKEN or not DB_URL:
        print("DASH_API_TOKEN and DASH_DB_URL required", file=sys.stderr)
        return 3

    corpus = _fetch_corpus()
    if not corpus:
        print("Empty corpus — nothing to replay. Exiting OK.")
        _write_junit([], False, "empty corpus")
        return 0

    # group by project
    by_proj: dict[str, list[int]] = {}
    for slug, cid, _ in corpus:
        by_proj.setdefault(slug, []).append(cid)

    rows: list[dict] = []
    total_reg = total_win = total_tie = total_err = 0
    delta_sum = 0.0; delta_n = 0

    for slug, cids in by_proj.items():
        label = f"ci_{int(time.time())}_{slug}"
        print(f"▶ {slug}: replay corpus_ids={cids}")
        try:
            start = _http("POST", f"/api/projects/{slug}/brainbench/runs",
                          {"corpus_ids": cids, "run_label": label})
        except Exception as e:
            print(f"  ✗ start failed: {e}", file=sys.stderr)
            rows.append({"project": slug, "run_id": "-", "status": "start_failed",
                         "total": 0, "wins": 0, "regr": 0, "ties": 0, "errs": 0, "avg_Δ": "-"})
            continue
        run_id = int(start.get("run_id") or 0)
        if not run_id:
            rows.append({"project": slug, "run_id": "-", "status": "no_run_id",
                         "total": 0, "wins": 0, "regr": 0, "ties": 0, "errs": 0, "avg_Δ": "-"})
            continue

        waited = 0; status = "running"; detail: dict[str, Any] = {}
        while waited < POLL_TIMEOUT:
            time.sleep(5); waited += 5
            try:
                detail = _http("GET", f"/api/projects/{slug}/brainbench/runs/{run_id}")
                status = (detail.get("run") or {}).get("status", "running")
            except Exception:
                pass
            if status in ("done", "failed"):
                break

        summ = ((detail.get("run") or {}).get("summary") or {})
        w = int(summ.get("wins") or 0); r = int(summ.get("regressions") or 0)
        t = int(summ.get("ties") or 0); e = int(summ.get("errors") or 0)
        tot = int(summ.get("total") or 0); avg = summ.get("avg_score_delta")
        total_win += w; total_reg += r; total_tie += t; total_err += e
        if isinstance(avg, (int, float)):
            delta_sum += float(avg); delta_n += 1
        rows.append({"project": slug, "run_id": run_id, "status": status,
                     "total": tot, "wins": w, "regr": r, "ties": t, "errs": e,
                     "avg_Δ": (round(avg, 3) if isinstance(avg, (int, float)) else "-")})

    avg_total = round(delta_sum / delta_n, 3) if delta_n else None
    print()
    _print_table(rows)
    print()
    print(f"TOTAL  W={total_win} R={total_reg} T={total_tie} E={total_err}  avg_Δ={avg_total}")

    failed = total_reg > 0 and (avg_total is not None) and (avg_total < FAIL_DELTA)
    reason = ""
    if failed:
        reason = f"regressions={total_reg}, avg_Δ={avg_total} < {FAIL_DELTA}"
        print(f"\nREGRESSION DETECTED: {reason}")
        _slack(f":rotating_light: BrainBench regression: {reason}\n```\n"
               + "\n".join(f"{r['project']} W={r['wins']} R={r['regr']} avg_Δ={r['avg_Δ']}" for r in rows)
               + "\n```")
    else:
        print("\nPASS")

    _write_junit(rows, failed, reason)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
