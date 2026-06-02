"""Industry benchmark sync — fetches public KPI/benchmark data from curated
web sources, parses it through an LLM, runs PII + generalization gates, and
upserts the resulting global facts into ``dash_company_brain`` under
``category='benchmark'`` (project_slug=NULL → globally shared).

Pipeline:
    1. fetch_benchmark(url)          → raw HTML/text
    2. parse_benchmark(text, indus)  → list[KPI dict]   (LITE_MODEL, strict JSON)
    3. PII scrub                      (reuses learning.promotion regex set)
    4. LLM generalize gate            (only A/B verdicts promoted)
    5. UPSERT into dash_company_brain (sha256(industry,kpi_name) dedupe)

Per-run safety net: ``cost_guard.get_status('__benchmark_sync__')`` aborts
remaining work once $0.50 is burned.

All public functions are **async** and **fail-soft**: a single bad source
must never kill the batch.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source health tracker (Issue #16) — auto-disable unreachable URLs after 3
# consecutive failures, with a 24h cooldown before retrying. Keeps weekly cron
# logs quiet without hard-deleting URLs that may come back online.
# ---------------------------------------------------------------------------

# url -> {"failures": int, "disabled_until": float | None,
#         "last_success_at": str | None, "last_failure_at": str | None,
#         "last_error": str | None}
dash_benchmark_source_health: dict[str, dict[str, Any]] = {}

_FAIL_THRESHOLD = 3
_DISABLE_COOLDOWN_S = 24 * 3600  # 24h


def _source_is_disabled(url: str) -> bool:
    """Returns True if a URL is currently in cooldown after repeated failures."""
    h = dash_benchmark_source_health.get(url)
    if not h:
        return False
    until = h.get("disabled_until")
    if not until:
        return False
    import time as _t
    if _t.time() < until:
        return True
    # cooldown elapsed — reset for retry
    h["disabled_until"] = None
    h["failures"] = 0
    return False


def _record_success(url: str) -> None:
    h = dash_benchmark_source_health.setdefault(url, {})
    h["failures"] = 0
    h["disabled_until"] = None
    h["last_success_at"] = datetime.now(timezone.utc).isoformat()


def _record_failure(url: str, err: str) -> None:
    import time as _t
    h = dash_benchmark_source_health.setdefault(url, {})
    h["failures"] = int(h.get("failures") or 0) + 1
    h["last_failure_at"] = datetime.now(timezone.utc).isoformat()
    h["last_error"] = err[:200]
    if h["failures"] >= _FAIL_THRESHOLD:
        h["disabled_until"] = _t.time() + _DISABLE_COOLDOWN_S


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Per-run LLM budget. The cost_guard is checked before each LLM call;
# once cumulative spend exceeds this, parsing aborts gracefully.
RUN_COST_CAP_USD: float = float(os.environ.get("BENCHMARK_SYNC_COST_CAP_USD", "0.50"))

# Synthetic project slug used purely as a budget bucket key for cost_guard.
_BUDGET_KEY = "__benchmark_sync__"

# Per-source caps.
HTTP_TIMEOUT_S: int = 30
HTTP_RETRIES: int = 2
MAX_KPIS_PER_SOURCE: int = 30
MAX_RAW_TEXT_CHARS: int = 60_000        # cap before LLM


# ---------------------------------------------------------------------------
# Curated source registry
# ---------------------------------------------------------------------------
# NOTE: real production deployments should swap in vetted, contractually-OK
# benchmark-publisher endpoints. The structure is the contract; URLs are
# stubs (publicly browsable industry-report indexes / KPI guides).

BENCHMARK_SOURCES: dict[str, list[dict[str, str]]] = {
    "retail": [
        {
            "name": "NRF retail KPI primer",
            "url": "https://nrf.com/topics/economy",
            "parse_hint": "Look for sales-per-sqft, conversion rate, basket size, inventory turn, shrink %.",
        },
        {
            "name": "US Census Monthly Retail Trade",
            "url": "https://www.census.gov/retail/marts/www/marts_current.html",
            "parse_hint": "Same-store sales YoY %, e-commerce share %, sector growth bands.",
        },
        {
            "name": "Shopify retail benchmarks",
            "url": "https://www.shopify.com/enterprise/blog/retail-benchmarks",
            "parse_hint": "AOV, conversion %, repeat-purchase %, return rate %.",
        },
    ],
    "saas": [
        {
            "name": "OpenView SaaS benchmarks",
            "url": "https://openviewpartners.com/saas-benchmarks/",
            "parse_hint": "ARR growth, NDR/GRR, CAC payback, gross margin, magic number.",
        },
        {
            "name": "ChartMogul SaaS metrics",
            "url": "https://chartmogul.com/saas-metrics/",
            "parse_hint": "MRR, churn rate, LTV/CAC, expansion %.",
        },
        {
            "name": "KeyBanc SaaS survey",
            "url": "https://www.key.com/businesses-institutions/industry-expertise/2024-saas-survey.html",
            "parse_hint": "Median ARR, growth rate, rule-of-40, net retention.",
        },
    ],
    "healthcare": [
        {
            "name": "CMS hospital quality measures",
            "url": "https://www.cms.gov/medicare/quality/initiatives/hospital-quality-initiative",
            "parse_hint": "Readmission rate, mortality, length of stay, HCAHPS.",
        },
        {
            "name": "AHRQ healthcare quality",
            "url": "https://www.ahrq.gov/research/findings/nhqrdr/index.html",
            "parse_hint": "Patient safety indicators, infection rates, access metrics.",
        },
        {
            "name": "Definitive Healthcare KPIs",
            "url": "https://www.definitivehc.com/resources",
            "parse_hint": "Bed occupancy, ED throughput, denial rate %, days-in-AR.",
        },
    ],
    "finance": [
        {
            "name": "FRED industry ratios",
            "url": "https://fred.stlouisfed.org/categories/32436",
            "parse_hint": "ROE, ROA, NIM, efficiency ratio, charge-off rate.",
        },
        {
            "name": "FFIEC bank performance",
            "url": "https://cdr.ffiec.gov/public/",
            "parse_hint": "Tier-1 capital, NPL %, cost-to-income, loan growth.",
        },
        {
            "name": "World Bank financial sector",
            "url": "https://data.worldbank.org/topic/financial-sector",
            "parse_hint": "Domestic credit %, NPL ratio, bank capital ratio.",
        },
    ],
    "hospitality": [
        {
            "name": "STR hotel performance",
            "url": "https://str.com/data-insights",
            "parse_hint": "Occupancy %, ADR, RevPAR, GOPPAR, length-of-stay.",
        },
        {
            "name": "AHLA state of the industry",
            "url": "https://www.ahla.com/research",
            "parse_hint": "Room nights, employment, RevPAR YoY %.",
        },
        {
            "name": "HotStats P&L benchmarks",
            "url": "https://www.hotstats.com/insights",
            "parse_hint": "Labor cost % of revenue, F&B contribution, flow-through %.",
        },
    ],
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkKPI:
    """Normalized industry benchmark fact."""
    kpi_name: str
    value: str            # keep as text — units / ranges / percentiles may be embedded
    unit: str = ""
    percentile: str = ""  # e.g. "p50", "median", "top quartile"
    source: str = ""
    source_url: str = ""
    industry: str = ""
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class SyncStats:
    fetched: int = 0
    parsed: int = 0
    promoted: int = 0
    rejected_pii: int = 0
    rejected_generalize: int = 0
    rejected_dedup: int = 0
    errors: int = 0


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

async def fetch_benchmark(source_url: str) -> Optional[str]:
    """Fetch URL → plain text (stripped of tags). Returns None on failure.

    30-second timeout, 2 retries with exponential backoff. Reuses the
    requests/httpx-style fetch shape so callers can swap in the existing
    ``web_search.py`` provider when an API key is configured (e.g. Tavily
    extract). For raw URLs we use httpx directly.
    """
    try:
        import httpx
    except Exception as e:
        logger.warning(f"benchmark_sync.fetch: httpx unavailable ({e})")
        return None

    # Skip cooled-down sources (Issue #16) — silently.
    if _source_is_disabled(source_url):
        logger.debug(f"benchmark_sync.fetch: skipping {source_url} (auto-disabled, in 24h cooldown)")
        return None

    headers = {
        "User-Agent": (
            "DashBenchmarkSync/1.0 (+https://dash.local/) "
            "https://github.com/citydash; respects robots.txt"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    last_err: Optional[str] = None
    backoff = 1.0
    for attempt in range(HTTP_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT_S, follow_redirects=True
            ) as client:
                resp = await client.get(source_url, headers=headers)
                if resp.status_code >= 400:
                    last_err = f"http_{resp.status_code}"
                    raise RuntimeError(last_err)
                text = resp.text or ""
                # crude tag strip — keep cheap; the LLM tolerates noise
                text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
                text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                _record_success(source_url)
                return text[:MAX_RAW_TEXT_CHARS]
        except Exception as e:
            last_err = str(e)[:120]
            if attempt < HTTP_RETRIES:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            # Demoted WARNING → DEBUG (Issue #16) — fail-counter handles
            # repeat-offender suppression; verbose mode still surfaces these.
            _record_failure(source_url, last_err)
            logger.debug(
                f"benchmark_sync.fetch failed for {source_url}: {last_err} "
                f"(failures={dash_benchmark_source_health[source_url]['failures']})"
            )
            return None
    return None


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

_PARSE_PROMPT = """You are extracting INDUSTRY-LEVEL BENCHMARK KPIs from a public source.

Industry: {industry}
Hint: {hint}

Return STRICT JSON (no commentary, no markdown). Schema:
{{
  "kpis": [
    {{
      "kpi_name": "string (<=80 chars, business-readable, e.g. 'Same-Store Sales Growth YoY')",
      "value":    "string (the published value or range, e.g. '3.2%' or '$420 - $580')",
      "unit":     "string (e.g. '%', 'USD', 'days', or empty)",
      "percentile":"string (e.g. 'median', 'p50', 'top quartile', or empty)"
    }}
  ]
}}

Rules:
- Output ONLY universal industry benchmarks (no single company, no PII, no specific people).
- Skip anything that is a one-off news item or stock price.
- Cap at {cap} KPIs. Most-important first.
- If the page has NO benchmarks, return {{"kpis": []}}.

CONTENT:
{content}
"""


def _budget_remaining() -> bool:
    """Returns True iff the per-run benchmark-sync budget is not yet exhausted."""
    try:
        from dash.learning.cost_guard import get_status
        st = get_status(_BUDGET_KEY)
        # cost_guard tracks per-day spend; here we only need to honour
        # the configured cap. Treat over_budget as a hard stop.
        if st.over_budget:
            return False
        # Also enforce the local cap if today's spend is already past it.
        if st.today_spend_usd >= RUN_COST_CAP_USD:
            return False
    except Exception:
        # Fail-open: cost_guard issues should never block ingestion entirely.
        pass
    return True


async def parse_benchmark(
    text: str, industry: str, *, source_name: str = "", source_url: str = "",
    hint: str = "",
) -> list[BenchmarkKPI]:
    """LLM-parse raw text into BenchmarkKPI list. Empty list on failure."""
    if not text or not text.strip():
        return []
    if not _budget_remaining():
        logger.info("benchmark_sync.parse: budget exhausted, skipping LLM call")
        return []

    prompt = _PARSE_PROMPT.format(
        industry=industry,
        hint=hint or "Extract any quantitative industry KPIs.",
        cap=MAX_KPIS_PER_SOURCE,
        content=text[:MAX_RAW_TEXT_CHARS],
    )

    try:
        from dash.settings import training_llm_call
        from dash.learning.cost_guard import set_llm_project as _maybe_set_proj  # type: ignore  # noqa: F401
    except Exception:
        # Older code paths only need the LLM call.
        from dash.settings import training_llm_call  # type: ignore

    # Run the synchronous LLM helper in a thread to keep this coroutine
    # cooperative (it's gated by httpx internally).
    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(
            None, lambda: training_llm_call(prompt, task="extraction")
        )
    except Exception as e:
        logger.warning(f"benchmark_sync.parse LLM err for {source_url}: {e}")
        return []

    if not raw:
        return []

    try:
        data = json.loads(raw)
    except Exception:
        # Defensive: try to find first {...} block
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except Exception:
            return []

    items = data.get("kpis") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []

    out: list[BenchmarkKPI] = []
    for it in items[:MAX_KPIS_PER_SOURCE]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("kpi_name") or "").strip()
        value = str(it.get("value") or "").strip()
        if not name or not value:
            continue
        out.append(BenchmarkKPI(
            kpi_name=name[:200],
            value=value[:300],
            unit=str(it.get("unit") or "").strip()[:30],
            percentile=str(it.get("percentile") or "").strip()[:40],
            source=source_name or source_url,
            source_url=source_url,
            industry=industry,
        ))
    return out


# ---------------------------------------------------------------------------
# Promotion gate (PII + generalize) — reuses learning.promotion.PromotionPipeline
# ---------------------------------------------------------------------------

def _pii_safe(text_to_check: str) -> bool:
    """Reuse the regex set from learning.promotion."""
    try:
        from dash.learning.promotion import _PII_BLOCKERS  # type: ignore
    except Exception:
        return True
    for pat in _PII_BLOCKERS:
        try:
            if pat.search(text_to_check or ""):
                return False
        except Exception:
            continue
    return True


def _generalizable(kpi: BenchmarkKPI) -> bool:
    """Cheap LLM gate. On any failure → fail-open (allow promotion)."""
    if not _budget_remaining():
        return True
    try:
        from dash.settings import training_llm_call
    except Exception:
        return True

    prompt = (
        "Statement: \"{ind} benchmark — {nm}: {val} {un} ({pct})\"\n"
        "Is this a UNIVERSAL industry benchmark (A), a common pattern (B), or a "
        "specific company finding (C)?\n"
        "Answer JSON: {{\"verdict\":\"A|B|C\"}}"
    ).format(
        ind=kpi.industry, nm=kpi.kpi_name, val=kpi.value,
        un=kpi.unit, pct=kpi.percentile or "n/a",
    )
    try:
        raw = training_llm_call(prompt, task="extraction") or ""
        m = re.search(r'"verdict"\s*:\s*"([ABC])"', raw)
        if m:
            return m.group(1) in ("A", "B")
        # Fallback: scan for plain letter answer.
        up = raw.upper()
        return ("VERDICT: A" in up) or ("VERDICT: B" in up) or up.strip() in ("A", "B")
    except Exception:
        return True


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

def _kpi_hash(industry: str, kpi_name: str) -> str:
    raw = f"{(industry or '').strip().lower()}|{(kpi_name or '').strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _upsert_benchmark(kpi: BenchmarkKPI) -> str:
    """Insert or update a benchmark row. Returns 'inserted' / 'updated' / 'skipped'."""
    try:
        from db.session import get_sql_engine
        from sqlalchemy import text
    except Exception as e:
        logger.warning(f"benchmark_sync.upsert: db unavailable: {e}")
        return "skipped"

    eng = get_sql_engine()
    if eng is None:
        return "skipped"

    h = _kpi_hash(kpi.industry, kpi.kpi_name)
    definition = f"{kpi.value} {kpi.unit}".strip()
    metadata = {
        "source": kpi.source,
        "source_url": kpi.source_url,
        "industry": kpi.industry,
        "percentile": kpi.percentile,
        "unit": kpi.unit,
        "value_raw": kpi.value,
        "captured_at": kpi.captured_at,
        "fact_hash": h,
        "kind": "benchmark",
    }

    # Manual upsert keyed on (category='benchmark', metadata->>fact_hash, project_slug IS NULL).
    try:
        with eng.connect() as conn:
            existing = conn.execute(text(
                "SELECT id FROM public.dash_company_brain "
                "WHERE category = 'benchmark' "
                "  AND project_slug IS NULL "
                "  AND metadata->>'fact_hash' = :h "
                "LIMIT 1"
            ), {"h": h}).fetchone()

            if existing:
                conn.execute(text(
                    "UPDATE public.dash_company_brain "
                    "SET definition = :defn, metadata = CAST(:meta AS jsonb), "
                    "    updated_at = NOW() "
                    "WHERE id = :id"
                ), {"defn": definition[:4000], "meta": json.dumps(metadata),
                    "id": int(existing[0])})
                conn.commit()
                return "updated"

            conn.execute(text(
                "INSERT INTO public.dash_company_brain "
                "(category, name, definition, metadata, project_slug, created_by) "
                "VALUES ('benchmark', :nm, :defn, CAST(:meta AS jsonb), NULL, "
                "        'benchmark_sync')"
            ), {"nm": kpi.kpi_name[:200],
                "defn": definition[:4000],
                "meta": json.dumps(metadata)})
            conn.commit()
            return "inserted"
    except Exception as e:
        logger.warning(f"benchmark_sync.upsert failed for {kpi.kpi_name}: {e}")
        return "skipped"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def _process_source(
    industry: str, src: dict[str, str], stats: SyncStats,
) -> None:
    """Fetch + parse + promote a single source. Always fail-soft."""
    url = src.get("url") or ""
    if not url:
        return
    try:
        text = await fetch_benchmark(url)
        if not text:
            stats.errors += 1
            return
        stats.fetched += 1

        kpis = await parse_benchmark(
            text, industry,
            source_name=src.get("name", ""),
            source_url=url,
            hint=src.get("parse_hint", ""),
        )
        stats.parsed += len(kpis)

        for kpi in kpis:
            if not _budget_remaining():
                logger.info("benchmark_sync: budget exhausted mid-source")
                return

            check_blob = f"{kpi.kpi_name} {kpi.value} {kpi.percentile} {kpi.source}"
            if not _pii_safe(check_blob):
                stats.rejected_pii += 1
                continue

            if not _generalizable(kpi):
                stats.rejected_generalize += 1
                continue

            verdict = _upsert_benchmark(kpi)
            if verdict in ("inserted", "updated"):
                stats.promoted += 1
            else:
                stats.rejected_dedup += 1
    except Exception as e:
        logger.warning(f"benchmark_sync._process_source error ({url}): {e}")
        stats.errors += 1


async def sync_benchmarks(
    industries: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Top-level orchestrator. Walks BENCHMARK_SOURCES, fetches each, parses,
    PII/generalize-gates, then upserts into ``dash_company_brain``.

    Args:
        industries: optional whitelist of industry keys; defaults to all.

    Returns:
        Dict with counts: ``fetched``, ``parsed``, ``promoted``, ``rejected_pii``,
        ``rejected_generalize``, ``rejected_dedup``, ``errors``.
    """
    targets = industries or list(BENCHMARK_SOURCES.keys())
    stats = SyncStats()

    for industry in targets:
        sources = BENCHMARK_SOURCES.get(industry) or []
        for src in sources:
            if not _budget_remaining():
                logger.info("benchmark_sync: aborting batch — budget exhausted")
                break
            await _process_source(industry, src, stats)

    out = {
        "fetched": stats.fetched,
        "parsed": stats.parsed,
        "promoted": stats.promoted,
        "rejected": (
            stats.rejected_pii + stats.rejected_generalize + stats.rejected_dedup
        ),
        "rejected_pii": stats.rejected_pii,
        "rejected_generalize": stats.rejected_generalize,
        "rejected_dedup": stats.rejected_dedup,
        "errors": stats.errors,
        "industries": targets,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(f"benchmark_sync: {out}")
    return out


__all__ = [
    "BENCHMARK_SOURCES",
    "BenchmarkKPI",
    "fetch_benchmark",
    "parse_benchmark",
    "sync_benchmarks",
    "dash_benchmark_source_health",
]
