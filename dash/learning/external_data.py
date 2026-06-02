"""External data adapter — plug-in registry for free open-data APIs.

Sources:
- FRED (US macro econ): https://api.stlouisfed.org/fred/series  (api key)
- Census (US demographics): https://api.census.gov/data
- World Bank: https://api.worldbank.org/v2/country
- Wikipedia: https://en.wikipedia.org/api/rest_v1/page/summary
- Wikidata: https://query.wikidata.org/sparql
- Alpha Vantage (markets): free tier, api key
- OECD: https://stats.oecd.org/SDMX-JSON
- News API (industry): newsapi.org (api key)

Each source = subclass of DataProvider w/ search() method.
Results cached in dash_external_facts table by query_hash.
Registry maps domain → list of providers prioritized for that domain.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_TTL_DAYS = {
    "fred": 30, "census": 90, "worldbank": 30, "wikipedia": 90,
    "wikidata": 90, "alpha_vantage": 1, "oecd": 30, "newsapi": 1,
}


@dataclass
class ExternalFact:
    source: str
    title: str
    value: str
    url: Optional[str] = None
    timestamp: Optional[str] = None
    confidence: float = 0.7
    metadata: dict = field(default_factory=dict)


class DataProvider(ABC):
    name: str = ""
    domains: list[str] = []     # which domains this is relevant to

    @abstractmethod
    def search(self, query: str, *, limit: int = 5) -> list[ExternalFact]:
        ...

    def is_configured(self) -> bool:
        return True


def _try_import_requests():
    """Lazy import of requests; logs a warning and returns None if unavailable."""
    try:
        import requests  # type: ignore
        return requests
    except Exception as e:
        logger.warning(f"requests library not available: {e}")
        return None


# ---------------------------------------------------------------------------
# FRED — US macro economic data (Fed Reserve)
# ---------------------------------------------------------------------------

class FREDProvider(DataProvider):
    name = "fred"
    domains = ["finance", "macro", "real_estate", "retail", "energy"]

    def is_configured(self) -> bool:
        try:
            from dash.admin.settings import get_setting
            if not get_setting("enable_fred"):
                return False
        except Exception:
            pass
        return bool(os.environ.get("FRED_API_KEY"))

    def search(self, query: str, *, limit: int = 5) -> list[ExternalFact]:
        api_key = os.environ.get("FRED_API_KEY")
        if not api_key:
            return []
        requests = _try_import_requests()
        if requests is None:
            return []
        try:
            r = requests.get(
                "https://api.stlouisfed.org/fred/series/search",
                params={"search_text": query, "api_key": api_key,
                        "file_type": "json", "limit": limit},
                timeout=15,
            )
            if r.status_code != 200:
                return []
            series = r.json().get("seriess", [])
            out = []
            for s in series[:limit]:
                out.append(ExternalFact(
                    source="fred", title=s.get("title", ""),
                    value=f"FRED series {s.get('id')}: {s.get('notes', '')[:300]}",
                    url=f"https://fred.stlouisfed.org/series/{s.get('id')}",
                    timestamp=s.get("last_updated"),
                    confidence=0.95,
                    metadata={"series_id": s.get("id"),
                              "frequency": s.get("frequency"),
                              "units": s.get("units")},
                ))
            return out
        except Exception as e:
            logger.warning(f"FRED search failed: {e}")
            return []


# ---------------------------------------------------------------------------
# Wikipedia — fact lookups, definitions
# ---------------------------------------------------------------------------

class WikipediaProvider(DataProvider):
    name = "wikipedia"
    domains = ["all"]   # universal

    def is_configured(self) -> bool:
        try:
            from dash.admin.settings import get_setting
            if not get_setting("enable_wikipedia"):
                return False
        except Exception:
            pass
        return True

    def search(self, query: str, *, limit: int = 3) -> list[ExternalFact]:
        requests = _try_import_requests()
        if requests is None:
            return []
        try:
            # 1. search via opensearch API
            r = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "opensearch", "search": query,
                        "limit": limit, "format": "json"},
                headers={"User-Agent": "Dash-DataAgent/1.0"},
                timeout=15,
            )
            if r.status_code != 200:
                return []
            data = r.json()
            titles = data[1] if len(data) > 1 else []
            urls = data[3] if len(data) > 3 else []
            # 2. fetch summary for top hits
            out = []
            for title, url in zip(titles[:limit], urls[:limit]):
                rs = requests.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
                    headers={"User-Agent": "Dash-DataAgent/1.0"},
                    timeout=15,
                )
                if rs.status_code == 200:
                    s = rs.json()
                    out.append(ExternalFact(
                        source="wikipedia", title=title,
                        value=(s.get("extract") or "")[:1000],
                        url=url, confidence=0.85,
                    ))
            return out
        except Exception as e:
            logger.warning(f"Wikipedia search failed: {e}")
            return []


# ---------------------------------------------------------------------------
# World Bank — country indicators
# ---------------------------------------------------------------------------

class WorldBankProvider(DataProvider):
    name = "worldbank"
    domains = ["finance", "macro", "social", "energy"]

    def is_configured(self) -> bool:
        try:
            from dash.admin.settings import get_setting
            if not get_setting("enable_world_bank"):
                return False
        except Exception:
            pass
        return True

    def search(self, query: str, *, limit: int = 5) -> list[ExternalFact]:
        requests = _try_import_requests()
        if requests is None:
            return []
        try:
            # search indicators
            r = requests.get(
                "https://api.worldbank.org/v2/indicator",
                params={"format": "json", "per_page": 1000,
                        "source": 2},  # WDI
                timeout=15,
            )
            if r.status_code != 200:
                return []
            data = r.json()
            if not isinstance(data, list) or len(data) < 2:
                return []
            # Filter by query keyword
            qlow = query.lower()
            out = []
            for ind in data[1] if isinstance(data[1], list) else []:
                name = (ind.get("name") or "").lower()
                if qlow in name:
                    out.append(ExternalFact(
                        source="worldbank", title=ind.get("name"),
                        value=(ind.get("sourceNote") or "")[:500],
                        url=f"https://data.worldbank.org/indicator/{ind.get('id')}",
                        confidence=0.90,
                        metadata={"id": ind.get("id")},
                    ))
                    if len(out) >= limit:
                        break
            return out
        except Exception as e:
            logger.warning(f"World Bank search failed: {e}")
            return []


# ---------------------------------------------------------------------------
# Alpha Vantage — financial markets (free tier)
# ---------------------------------------------------------------------------

class AlphaVantageProvider(DataProvider):
    name = "alpha_vantage"
    domains = ["finance", "markets"]

    def is_configured(self) -> bool:
        try:
            from dash.admin.settings import get_setting
            if not get_setting("enable_alpha_vantage"):
                return False
        except Exception:
            pass
        return bool(os.environ.get("ALPHA_VANTAGE_API_KEY"))

    def search(self, query: str, *, limit: int = 5) -> list[ExternalFact]:
        api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
        if not api_key:
            return []
        requests = _try_import_requests()
        if requests is None:
            return []
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={"function": "SYMBOL_SEARCH", "keywords": query,
                        "apikey": api_key},
                timeout=15,
            )
            if r.status_code != 200:
                return []
            data = r.json()
            out = []
            for m in data.get("bestMatches", [])[:limit]:
                out.append(ExternalFact(
                    source="alpha_vantage",
                    title=m.get("2. name", ""),
                    value=f"Symbol {m.get('1. symbol')} on {m.get('4. region')}",
                    url=None,
                    confidence=0.95,
                    metadata={"symbol": m.get("1. symbol")},
                ))
            return out
        except Exception as e:
            logger.warning(f"Alpha Vantage search failed: {e}")
            return []


# ---------------------------------------------------------------------------
# Wikidata — structured KG facts via SPARQL
# ---------------------------------------------------------------------------

class WikidataProvider(DataProvider):
    name = "wikidata"
    domains = ["all"]

    def is_configured(self) -> bool:
        try:
            from dash.admin.settings import get_setting
            if not get_setting("enable_wikidata"):
                return False
        except Exception:
            pass
        return True

    def search(self, query: str, *, limit: int = 5) -> list[ExternalFact]:
        requests = _try_import_requests()
        if requests is None:
            return []
        try:
            # Escape double-quotes in query to avoid SPARQL injection issues
            safe_q = query.replace('"', '\\"')
            sparql = f"""
SELECT ?item ?itemLabel ?desc WHERE {{
  ?item rdfs:label "{safe_q}"@en.
  ?item schema:description ?desc.
  FILTER(LANG(?desc) = "en")
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}} LIMIT {limit}
"""
            r = requests.get(
                "https://query.wikidata.org/sparql",
                params={"query": sparql, "format": "json"},
                headers={"User-Agent": "Dash-DataAgent/1.0"},
                timeout=20,
            )
            if r.status_code != 200:
                return []
            bindings = r.json().get("results", {}).get("bindings", [])
            out = []
            for b in bindings[:limit]:
                item_uri = b.get("item", {}).get("value", "")
                label = b.get("itemLabel", {}).get("value", "")
                desc = b.get("desc", {}).get("value", "")
                out.append(ExternalFact(
                    source="wikidata", title=label,
                    value=desc[:500], url=item_uri,
                    confidence=0.85,
                ))
            return out
        except Exception as e:
            logger.warning(f"Wikidata search failed: {e}")
            return []


# ---------------------------------------------------------------------------
# Registry + dispatch
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, DataProvider] = {}


def register(provider: DataProvider):
    _PROVIDERS[provider.name] = provider


# Register defaults at import
register(FREDProvider())
register(WikipediaProvider())
register(WorldBankProvider())
register(AlphaVantageProvider())
register(WikidataProvider())


def get_providers_for_domain(domain: str) -> list[DataProvider]:
    """Return providers relevant for given domain (always includes 'all')."""
    return [p for p in _PROVIDERS.values()
            if domain in p.domains or "all" in p.domains]


def fetch_facts(query: str, *, domain: str = "all", limit_per_source: int = 3,
                use_cache: bool = True) -> list[ExternalFact]:
    """Fetch from all configured providers for the given domain.

    Cache lookup first; on miss, call API + store cache.
    Returns flat list of ExternalFact across providers.
    Never raises — provider errors are logged and skipped.
    """
    out: list[ExternalFact] = []
    try:
        providers = get_providers_for_domain(domain)
    except Exception as e:
        logger.warning(f"provider lookup failed: {e}")
        return out
    for p in providers:
        try:
            if not p.is_configured():
                continue
            # Cache check
            if use_cache:
                cached = _cache_get(query, p.name)
                if cached is not None:
                    out.extend(cached)
                    continue
            # Live call
            results = p.search(query, limit=limit_per_source)
            if results and use_cache:
                _cache_put(query, p.name, results,
                           ttl_days=CACHE_TTL_DAYS.get(p.name, 7))
            out.extend(results)
        except Exception as e:
            logger.warning(f"Provider {p.name} failed: {e}")
    return out


def _query_hash(query: str, source: str) -> str:
    return hashlib.sha256(f"{source}:{query.lower().strip()}".encode()).hexdigest()


def _cache_get(query: str, source: str) -> Optional[list[ExternalFact]]:
    try:
        from sqlalchemy import text  # type: ignore
        from db.session import get_sql_engine  # type: ignore
        eng = get_sql_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT result_json FROM public.dash_external_facts "
                "WHERE query_hash = :h AND (expires_at IS NULL OR expires_at > NOW()) "
                "ORDER BY fetched_at DESC LIMIT 1"
            ), {"h": _query_hash(query, source)}).fetchone()
        if row is None:
            return None
        data = row[0] or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        facts = data.get("facts", []) if isinstance(data, dict) else []
        return [ExternalFact(**f) for f in facts]
    except Exception:
        return None


def _cache_put(query: str, source: str, facts: list[ExternalFact], ttl_days: int):
    try:
        from sqlalchemy import text  # type: ignore
        from db.session import get_sql_engine  # type: ignore
        eng = get_sql_engine()
        payload = {"facts": [asdict(f) for f in facts]}
        with eng.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_external_facts "
                "(query_hash, source_type, query_text, result_json, "
                " expires_at, cost_usd, http_status) "
                "VALUES (:h, :s, :q, :j, NOW() + :ttl::interval, 0, 200) "
                "ON CONFLICT (query_hash) DO UPDATE SET "
                " result_json = EXCLUDED.result_json, "
                " fetched_at = NOW(), "
                " expires_at = EXCLUDED.expires_at"
            ), {
                "h": _query_hash(query, source), "s": source, "q": query,
                "j": json.dumps(payload), "ttl": f"{ttl_days} days",
            })
            conn.commit()
    except Exception as e:
        logger.debug(f"cache_put failed: {e}")


def is_configured() -> dict:
    return {p.name: p.is_configured() for p in _PROVIDERS.values()}
