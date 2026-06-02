"""Pure-python entity + link extractors. NO LLM imports.

Patterns supported:
- @PersonName        → person
- [[Concept]]        → concept
- #tag               → tag
- https?://...       → url
- "Acme Inc" etc.    → company  (capitalized n-gram near corp keywords)

Page-role rules:
- page_kind == 'meeting' + @X → link (meeting_page, attended, X)
- page_kind == 'profile' + 'works_at: Company' line → link (person, works_at, company)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ── Regex patterns ─────────────────────────────────────────────────────────

_RE_PERSON = re.compile(r"@([A-Z][a-z]+(?:[A-Z][a-z]+)?(?:\s+[A-Z][a-z]+)?)")
_RE_CONCEPT = re.compile(r"\[\[([^\]]+)\]\]")
_RE_TAG = re.compile(r"(?<![A-Za-z0-9_])#([a-z][a-z0-9_-]*)")
_RE_URL = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)

# Capitalized N-gram (2-3 words) followed by a company suffix.
_COMPANY_SUFFIXES = r"(?:Inc|Corp|LLC|Ltd|LLP|Co|GmbH|AG|SA|PLC|NV|Pty|BV)"
_RE_COMPANY = re.compile(
    rf"\b([A-Z][A-Za-z0-9&]+(?:\s+[A-Z][A-Za-z0-9&]+){{0,2}})(?:,?\s+{_COMPANY_SUFFIXES})\.?\b"
)

# Profile 'works_at:' style line: works_at: Acme Inc
_RE_WORKS_AT = re.compile(r"works[_ ]?at\s*:\s*([^\n]+)", re.IGNORECASE)


# ── Normalization ──────────────────────────────────────────────────────────

_PUNCT_RE = re.compile(r"[^\w\s-]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_name(s: str) -> str:
    """Lowercase, strip punctuation (keep hyphen + underscore), collapse whitespace."""
    if not s:
        return ""
    s = s.strip().lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


# ── Entity extraction ──────────────────────────────────────────────────────

def _add(out: list[dict], seen: set, kind: str, name: str, span: tuple[int, int]) -> None:
    name = (name or "").strip().strip(".,;:!?")
    if not name:
        return
    norm = normalize_name(name)
    if not norm:
        return
    key = (kind, norm)
    if key in seen:
        return
    seen.add(key)
    out.append({"kind": kind, "name": name, "name_normalized": norm, "span": list(span)})


def extract_entities(text: str, page_kind: str | None = None) -> list[dict]:
    """Extract entity mentions from text. Returns list of {kind,name,name_normalized,span}."""
    if not text:
        return []
    out: list[dict] = []
    seen: set = set()

    for m in _RE_PERSON.finditer(text):
        _add(out, seen, "person", m.group(1), m.span(1))
    for m in _RE_CONCEPT.finditer(text):
        _add(out, seen, "concept", m.group(1), m.span(1))
    for m in _RE_TAG.finditer(text):
        _add(out, seen, "tag", m.group(1), m.span(1))
    for m in _RE_URL.finditer(text):
        url = m.group(0).rstrip(".,);:")
        _add(out, seen, "url", url, (m.start(), m.start() + len(url)))
    for m in _RE_COMPANY.finditer(text):
        _add(out, seen, "company", m.group(0), m.span(0))

    # profile page: works_at line → company entity
    if page_kind == "profile":
        for m in _RE_WORKS_AT.finditer(text):
            comp = m.group(1).strip().rstrip(".,;")
            if comp:
                _add(out, seen, "company", comp, m.span(1))

    return out


# ── Link extraction ────────────────────────────────────────────────────────

def extract_links(
    text: str,
    page_kind: str | None = None,
    source_id: int | None = None,
) -> list[dict]:
    """Extract relationships from text.

    Returns list of {src, rel, dst, confidence, source_ref}.
    `src` and `dst` are dicts with {kind, name, name_normalized}.
    """
    if not text:
        return []
    entities = extract_entities(text, page_kind=page_kind)
    links: list[dict] = []
    seen: set = set()

    src_ref = f"page:{source_id}" if source_id is not None else None

    def _emit(src: dict, rel: str, dst: dict, conf: float = 1.0) -> None:
        key = (src["kind"], src["name_normalized"], rel, dst["kind"], dst["name_normalized"])
        if key in seen:
            return
        seen.add(key)
        links.append({
            "src": {"kind": src["kind"], "name": src["name"], "name_normalized": src["name_normalized"]},
            "rel": rel,
            "dst": {"kind": dst["kind"], "name": dst["name"], "name_normalized": dst["name_normalized"]},
            "confidence": conf,
            "source_ref": src_ref,
        })

    # generic "mentions" links: meta-entity for the page itself if page_kind
    page_entity: dict | None = None
    if page_kind:
        sid = source_id if source_id is not None else "anon"
        page_entity = {
            "kind": f"{page_kind}_page",
            "name": f"{page_kind}#{sid}",
            "name_normalized": normalize_name(f"{page_kind} {sid}"),
        }

    if page_entity:
        for e in entities:
            _emit(page_entity, "mentions", e, 1.0)

    # Page-role specific edges
    if page_kind == "meeting" and page_entity:
        for e in entities:
            if e["kind"] == "person":
                _emit(page_entity, "attended", e, 1.0)

    if page_kind == "profile":
        # link extracted persons → companies via works_at: line
        # The first person on the page (if any) is treated as the profile owner.
        owner = next((e for e in entities if e["kind"] == "person"), None)
        for m in _RE_WORKS_AT.finditer(text):
            comp_name = m.group(1).strip().rstrip(".,;")
            if not comp_name:
                continue
            comp_norm = normalize_name(comp_name)
            comp = {"kind": "company", "name": comp_name, "name_normalized": comp_norm}
            if owner:
                _emit(owner, "works_at", comp, 1.0)
            elif page_entity:
                _emit(page_entity, "about_company", comp, 1.0)

    return links


# ── DB upsert ──────────────────────────────────────────────────────────────

def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _upsert_entity(conn, project: str | None, ent: dict) -> int:
    """Upsert entity, return id."""
    from sqlalchemy import text
    row = conn.execute(
        text("""
            INSERT INTO dash.dash_entities
                (project_slug, kind, name, name_normalized)
            VALUES (:p, :k, :n, :nn)
            ON CONFLICT (project_slug, kind, name_normalized) DO UPDATE
                SET name = EXCLUDED.name
            RETURNING id
        """),
        {"p": project, "k": ent["kind"], "n": ent["name"], "nn": ent["name_normalized"]},
    ).fetchone()
    return int(row[0])


def link_text(
    project: str,
    text: str,
    page_kind: str | None = None,
    source_ref: str | None = None,
) -> dict:
    """Extract entities + links and upsert to DB.

    Returns {entities_created, links_created}.
    """
    from sqlalchemy import text as sa_text

    if not text:
        return {"entities_created": 0, "links_created": 0}

    # parse source_id from source_ref of form "page:123" if possible
    source_id: int | None = None
    if source_ref:
        try:
            if ":" in source_ref:
                source_id = int(source_ref.split(":", 1)[1])
        except Exception:
            source_id = None

    ents = extract_entities(text, page_kind=page_kind)
    links = extract_links(text, page_kind=page_kind, source_id=source_id)

    eng = _engine()
    if eng is None:
        return {"entities_created": 0, "links_created": 0, "error": "no_engine"}

    ents_created = 0
    links_created = 0
    id_cache: dict[tuple[str, str], int] = {}

    with eng.begin() as conn:
        for e in ents:
            key = (e["kind"], e["name_normalized"])
            if key in id_cache:
                continue
            before = conn.execute(
                sa_text("""
                    SELECT id FROM dash.dash_entities
                    WHERE (CAST(:p AS TEXT) IS NULL AND project_slug IS NULL OR project_slug = :p)
                      AND kind = :k AND name_normalized = :nn
                """),
                {"p": project, "k": key[0], "nn": key[1]},
            ).fetchone()
            eid = _upsert_entity(conn, project, e)
            id_cache[key] = eid
            if before is None:
                ents_created += 1

        # For links, ensure src/dst entities are upserted too (they may include
        # synthetic page entities or works_at companies not present in `ents`).
        for ln in links:
            for side in ("src", "dst"):
                s = ln[side]
                key = (s["kind"], s["name_normalized"])
                if key not in id_cache:
                    before = conn.execute(
                        sa_text("""
                            SELECT id FROM dash.dash_entities
                            WHERE (CAST(:p AS TEXT) IS NULL AND project_slug IS NULL OR project_slug = :p)
                              AND kind = :k AND name_normalized = :nn
                        """),
                        {"p": project, "k": key[0], "nn": key[1]},
                    ).fetchone()
                    eid = _upsert_entity(conn, project, s)
                    id_cache[key] = eid
                    if before is None:
                        ents_created += 1

            src_id = id_cache[(ln["src"]["kind"], ln["src"]["name_normalized"])]
            dst_id = id_cache[(ln["dst"]["kind"], ln["dst"]["name_normalized"])]
            res = conn.execute(
                sa_text("""
                    INSERT INTO dash.dash_entity_links
                        (project_slug, src_entity_id, rel, dst_entity_id, source_ref, confidence)
                    VALUES (:p, :s, :r, :d, :ref, :c)
                    ON CONFLICT (project_slug, src_entity_id, rel, dst_entity_id) DO UPDATE
                        SET source_ref = COALESCE(EXCLUDED.source_ref, dash.dash_entity_links.source_ref),
                            confidence = GREATEST(dash.dash_entity_links.confidence, EXCLUDED.confidence)
                    RETURNING (xmax = 0) AS inserted
                """),
                {
                    "p": project,
                    "s": src_id,
                    "r": ln["rel"],
                    "d": dst_id,
                    "ref": source_ref or ln.get("source_ref"),
                    "c": float(ln.get("confidence", 1.0)),
                },
            ).fetchone()
            if res and bool(res[0]):
                links_created += 1

    return {"entities_created": ents_created, "links_created": links_created}
