"""OKF export — emit a project's knowledge as an Open Knowledge Format bundle.

OKF (https://github.com/GoogleCloudPlatform/knowledge-catalog) = a directory of
markdown files with YAML frontmatter: portable, git-diffable, vendor-neutral.

This endpoint is strictly READ-ONLY: it SELECTs the existing knowledge (table
metadata, KG join graph, verified query bank, brain facts) and streams a zip
containing the markdown bundle + a self-contained interactive viz.html. It
writes nothing to the DB, schema, or knowledge volume. 2026-06-14.
"""
from __future__ import annotations

import io
import json
import re
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

router = APIRouter(prefix="/api/projects", tags=["okf"])


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "x"


def _build_bundle(slug: str) -> dict[str, str]:
    """Walk existing knowledge → {path: content}. READ-ONLY (SELECT only)."""
    files: dict[str, str] = {}
    eng = _engine()
    # AUTOCOMMIT so a single failing read (e.g. COUNT on a half-built derived
    # table) can't poison the transaction and abort every subsequent query.
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        tbls = [
            r[0] for r in c.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :s AND table_type = 'BASE TABLE'"
            ), {"s": slug}).fetchall()
            if not r[0].startswith("_") and not r[0].endswith("__bak")
        ]
        # KG join edges
        joins: dict[str, list[str]] = {}
        try:
            for s, _p, o in c.execute(text(
                "SELECT subject, predicate, object FROM public.dash_knowledge_triples "
                "WHERE predicate = 'joins_with'"
            )).fetchall():
                joins.setdefault(s, []).append(o)
        except Exception:
            pass
        # verified queries
        try:
            qs = c.execute(text(
                "SELECT question, sql, status, uses FROM public.dash_query_patterns "
                "ORDER BY uses DESC NULLS LAST LIMIT 200"
            )).fetchall()
        except Exception:
            qs = []
        # brain facts
        try:
            facts = c.execute(text(
                "SELECT fact FROM public.dash_company_brain LIMIT 500"
            )).fetchall()
        except Exception:
            facts = []

        # table concepts
        for t in tbls:
            cols = c.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t ORDER BY ordinal_position"
            ), {"s": slug, "t": t}).fetchall()
            try:
                rc = c.execute(text(f'SELECT COUNT(*) FROM "{slug}"."{t}"')).scalar() or 0
            except Exception:
                rc = 0
            body = [
                "---", "type: Table", f"title: {t}", f"resource: {slug}.{t}",
                "tags: [data]", "---", "", "# Schema", "",
                f"_{rc:,} rows_", "", "| Column | Type |", "|---|---|",
            ]
            body += [f"| `{cn}` | {ct} |" for cn, ct in cols]
            if joins.get(t):
                body += ["", "# Joins"] + [f"- joins_with [{j}](/tables/{_slugify(j)}.md)" for j in joins[t]]
            files[f"tables/{_slugify(t)}.md"] = "\n".join(body) + "\n"

    # verified-query concepts
    for q, sql, st, uses in qs:
        if not q:
            continue
        files[f"queries/{_slugify(q)[:60]}.md"] = (
            f"---\ntype: Verified Query\ntitle: {q}\nstatus: {st}\nuses: {uses or 0}\n---\n\n"
            f"```sql\n{(sql or '').strip()[:1200]}\n```\n"
        )

    # facts as one concept
    if facts:
        lines = ["---", "type: Reference", "title: Brain Facts", "tags: [brain]", "---", "", "# Facts", ""]
        lines += [f"- {(f[0] or '').strip()[:300]}" for f in facts if f[0]]
        files["facts/brain.md"] = "\n".join(lines) + "\n"

    # index.md (progressive disclosure)
    idx = [f"# {slug} — Knowledge Bundle\n", "## Tables"]
    idx += [f"* [{p[7:-3]}](tables/{p[7:]})" for p in sorted(files) if p.startswith("tables/")]
    idx += ["", "## Verified Queries"]
    idx += [f"* [{p[8:-3]}](queries/{p[8:]})" for p in sorted(files) if p.startswith("queries/")]
    files["index.md"] = "\n".join(idx) + "\n"
    files["log.md"] = f"# Update Log\n\n## export\n* **Export**: OKF bundle generated for `{slug}`.\n"
    return files


_VIZ_HTML = """<!doctype html><html><head><meta charset=utf-8><title>OKF — __NAME__</title>
<script src=https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.2/cytoscape.min.js></script>
<script src=https://cdn.jsdelivr.net/npm/marked/marked.min.js></script>
<style>body{margin:0;font:14px system-ui;display:flex;height:100vh;color:#1c1b18}
#cy{flex:1;background:#faf9f5}#side{width:420px;border-left:1px solid #e5e0d8;overflow:auto;padding:16px;background:#fff}
#search{width:100%;padding:8px;border:1px solid #ccc;border-radius:8px;margin-bottom:10px;box-sizing:border-box}
.t{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;background:#eee;margin-bottom:8px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #eee;padding:4px 8px;text-align:left;font-size:13px}
pre{background:#f5f3ee;padding:10px;border-radius:8px;overflow:auto}
.leg{position:absolute;top:10px;left:10px;background:#fffd;padding:8px 10px;border-radius:8px;font-size:12px}
.leg b{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}</style></head><body>
<div id=cy></div><div class=leg id=leg></div><div id=side><input id=search placeholder="search…"><div id=detail>Click a node.</div></div>
<script>const G=__GRAPH__;const COL={Table:'#3f7fb0','Verified Query':'#2f8f83',Reference:'#c98a2e',Concept:'#888'};
const cy=cytoscape({container:document.getElementById('cy'),elements:G,style:[{selector:'node',style:{'background-color':e=>COL[e.data('type')]||'#888','label':'data(label)','font-size':9,'width':22,'height':22,'text-wrap':'wrap','text-max-width':90}},{selector:'edge',style:{'width':1,'line-color':'#cbb','target-arrow-color':'#cbb','target-arrow-shape':'triangle','curve-style':'bezier','opacity':.5}},{selector:'.sel',style:{'border-width':3,'border-color':'#c2683f'}}],layout:{name:'cose',animate:false,nodeRepulsion:9000,idealEdgeLength:90}});
document.getElementById('leg').innerHTML=[...new Set(G.nodes.map(n=>n.data.type))].map(t=>`<div><b style="background:${COL[t]||'#888'}"></b>${t}</div>`).join('');
function show(d){let h=`<span class=t style="background:${COL[d.type]||'#888'}22">${d.type}${d.status?' · '+d.status:''}</span><h2>${d.label}</h2>`;if(d.fm.resource)h+=`<div style=color:#888;font-size:12px>${d.fm.resource}</div>`;h+=marked.parse(d.body||'');document.getElementById('detail').innerHTML=h;}
cy.on('tap','node',e=>{cy.elements().removeClass('sel');e.target.addClass('sel');show(e.target.data());});
document.getElementById('search').oninput=e=>{const q=e.target.value.toLowerCase();cy.nodes().forEach(n=>{const d=n.data();n.style('opacity',!q||(d.label+d.id+d.type).toLowerCase().includes(q)?1:.12);});};
</script></body></html>"""


def _viz(files: dict[str, str], name: str) -> str:
    nodes, edges = [], []
    for path, raw in files.items():
        if not path.endswith(".md") or path in ("index.md", "log.md"):
            continue
        cid = path[:-3]
        fm, body = {}, raw
        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
        if m:
            body = m.group(2)
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip()
        nodes.append({"data": {"id": cid, "label": fm.get("title", cid.split("/")[-1]),
                               "type": fm.get("type", "Concept"), "status": fm.get("status", ""),
                               "body": body, "fm": fm}})
        for tgt in re.findall(r"\]\((/[^)]+?)\.md\)", body):
            edges.append({"data": {"source": cid, "target": tgt.lstrip("/")}})
    ids = {n["data"]["id"] for n in nodes}
    edges = [e for e in edges if e["data"]["target"] in ids]
    return _VIZ_HTML.replace("__GRAPH__", json.dumps({"nodes": nodes, "edges": edges})).replace("__NAME__", name)


@router.get("/{slug}/okf-export")
def okf_export(slug: str, request: Request):
    """Download the project's knowledge as an OKF bundle zip (+ viz.html).
    Read-only; writes nothing. Editor role required."""
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, required_role="editor"):
        raise HTTPException(403, "Editor access required")

    files = _build_bundle(slug)
    files["viz.html"] = _viz(files, slug)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in files.items():
            z.writestr(f"{slug}-okf/{path}", content)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}-okf-bundle.zip"'},
    )
