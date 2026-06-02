"""Pull DAX measures, relationships, descriptions from a Fabric/Power BI
semantic model via XMLA endpoint.

Authentication: Service Principal (Entra ID) — reuses MS_CLIENT_ID/
MS_CLIENT_SECRET/MS_TENANT_ID env vars from app/sharepoint.py pattern.

Strategy: XMLA endpoint accepts SOAP-formatted DMV queries. We use the
DMV (Dynamic Management Views) approach because it's broadly supported:

    SELECT * FROM $SYSTEM.TMSCHEMA_MEASURES
    SELECT * FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS
    SELECT * FROM $SYSTEM.TMSCHEMA_TABLES
    SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS

Or higher-level:

    SELECT * FROM $SYSTEM.MDSCHEMA_MEASURES
    SELECT * FROM $SYSTEM.DBSCHEMA_TABLES

Returns parsed dicts. Falls back to no-op + warn if pyadomd / requests
SOAP fails.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class XMLAMeasure:
    name: str
    table: str
    expression: str          # DAX
    description: Optional[str] = None
    format_string: Optional[str] = None
    folder: Optional[str] = None


@dataclass
class XMLARelationship:
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    cardinality: Optional[str] = None
    cross_filter: Optional[str] = None


@dataclass
class XMLAResult:
    workspace: str
    dataset: str
    measures: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    error: Optional[str] = None


def _get_aad_token() -> Optional[str]:
    """Acquire AAD token via MSAL (Service Principal). Returns None on failure."""
    try:
        import msal
    except ImportError:
        logger.warning("msal not installed; XMLA pull disabled")
        return None

    client_id = os.environ.get("MS_CLIENT_ID")
    client_secret = os.environ.get("MS_CLIENT_SECRET")
    tenant_id = os.environ.get("MS_TENANT_ID", "common")
    if not client_id or not client_secret:
        logger.warning("MS_CLIENT_ID/SECRET not set; XMLA pull disabled")
        return None

    try:
        app = msal.ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        result = app.acquire_token_for_client(
            scopes=["https://analysis.windows.net/powerbi/api/.default"]
        )
    except Exception as e:
        logger.warning(f"MSAL client error: {e}")
        return None

    if isinstance(result, dict) and "access_token" in result:
        return result["access_token"]
    desc = ""
    if isinstance(result, dict):
        desc = result.get("error_description", "") or result.get("error", "")
    logger.warning(f"AAD token acquisition failed: {desc}")
    return None


def _xmla_soap_query(
    endpoint: str,
    token: str,
    dmv: str,
    catalog: str = "",
) -> Optional[str]:
    """Send a SOAP Discover/Execute request to the XMLA endpoint.

    Returns raw XML response on success, None on failure.
    """
    try:
        import requests
    except ImportError:
        logger.warning("requests not installed; XMLA pull disabled")
        return None

    soap_envelope = f'''<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <Execute xmlns="urn:schemas-microsoft-com:xml-analysis">
      <Command>
        <Statement>{dmv}</Statement>
      </Command>
      <Properties>
        <PropertyList>
          <Catalog>{catalog}</Catalog>
        </PropertyList>
      </Properties>
    </Execute>
  </soap:Body>
</soap:Envelope>'''

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "urn:schemas-microsoft-com:xml-analysis:Execute",
    }
    try:
        r = requests.post(endpoint, data=soap_envelope, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.text
        logger.warning(f"XMLA query failed {r.status_code}: {r.text[:300]}")
    except Exception as e:
        logger.warning(f"XMLA request error: {e}")
    return None


def _iter_rows(xml_text: str):
    """Yield dicts (one per <row>) from an XMLA SOAP response.

    Handles namespace-prefixed tags by matching on local-name.
    """
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        logger.warning(f"Failed to parse XMLA XML: {e}")
        return
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag != "row":
            continue
        data = {}
        for child in elem:
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            data[ctag] = (child.text or "")
        yield data


def _parse_measures_xml(xml_text: str) -> list:
    """Parse the SOAP response for $SYSTEM.MDSCHEMA_MEASURES rows."""
    measures: list = []
    try:
        for data in _iter_rows(xml_text):
            measures.append(XMLAMeasure(
                name=data.get("MEASURE_NAME") or data.get("Name", ""),
                table=data.get("MEASUREGROUP_NAME") or data.get("Table", ""),
                expression=data.get("EXPRESSION") or "",
                description=data.get("DESCRIPTION") or data.get("Description"),
                format_string=data.get("DEFAULT_FORMAT_STRING"),
                folder=data.get("MEASURE_DISPLAY_FOLDER"),
            ))
    except Exception as e:
        logger.warning(f"Failed to parse measures XML: {e}")
    return measures


def _parse_relationships_xml(xml_text: str) -> list:
    """Parse $SYSTEM.TMSCHEMA_RELATIONSHIPS response (best-effort).

    TMSCHEMA_RELATIONSHIPS exposes IDs (FromTableID/ToTableID/...); the
    caller should join against TMSCHEMA_TABLES + TMSCHEMA_COLUMNS to
    resolve names. Here we capture whatever name-like fields we find and
    leave ID-only rows for later enrichment.
    """
    rels: list = []
    try:
        for data in _iter_rows(xml_text):
            from_table = (
                data.get("FromTableName")
                or data.get("FROM_TABLE")
                or data.get("FromTableID")
                or ""
            )
            to_table = (
                data.get("ToTableName")
                or data.get("TO_TABLE")
                or data.get("ToTableID")
                or ""
            )
            from_col = (
                data.get("FromColumnName")
                or data.get("FROM_COLUMN")
                or data.get("FromColumnID")
                or ""
            )
            to_col = (
                data.get("ToColumnName")
                or data.get("TO_COLUMN")
                or data.get("ToColumnID")
                or ""
            )
            rels.append(XMLARelationship(
                from_table=str(from_table),
                from_col=str(from_col),
                to_table=str(to_table),
                to_col=str(to_col),
                cardinality=data.get("FromCardinality") or data.get("CARDINALITY"),
                cross_filter=data.get("CrossFilteringBehavior"),
            ))
    except Exception as e:
        logger.warning(f"Failed to parse relationships XML: {e}")
    return rels


def pull_semantic_model(
    workspace: str,
    dataset: str,
    *,
    endpoint_override: Optional[str] = None,
) -> XMLAResult:
    """Pull DAX measures + relationships from a Fabric/Power BI semantic model.

    Args:
        workspace: Power BI workspace name (e.g. "ContosoSales")
        dataset: dataset/semantic model name within workspace
        endpoint_override: full XMLA URL if non-standard

    Returns XMLAResult with measures + relationships populated, or
    error field set if auth/connection failed.
    """
    result = XMLAResult(workspace=workspace, dataset=dataset)

    token = _get_aad_token()
    if token is None:
        result.error = "AAD token unavailable"
        return result

    endpoint = endpoint_override or (
        f"https://api.powerbi.com/v1.0/myorg/Workspaces/{workspace}"
    )

    # Pull measures via MDSCHEMA (broadly supported high-level view).
    xml = _xmla_soap_query(
        endpoint, token,
        "SELECT * FROM $SYSTEM.MDSCHEMA_MEASURES",
        catalog=dataset,
    )
    if xml:
        result.measures = _parse_measures_xml(xml)

    # Pull relationships via TMSCHEMA (Tabular).
    xml2 = _xmla_soap_query(
        endpoint, token,
        "SELECT * FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS",
        catalog=dataset,
    )
    if xml2:
        result.relationships = _parse_relationships_xml(xml2)

    if not result.measures and not result.relationships:
        result.error = result.error or "no measures or relationships returned"

    return result


def import_to_dash(
    result: XMLAResult,
    project_slug: str,
    source_id: int,
) -> dict:
    """Insert pulled measures into dash_rules_db + dash_company_brain.

    Returns dict with counts: {measures_inserted, brain_entries_inserted, errors}.
    """
    counts: dict = {
        "measures_inserted": 0,
        "brain_entries_inserted": 0,
        "errors": [],
    }
    if result.error:
        counts["errors"].append(result.error)
        return counts

    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
    except Exception as e:  # pragma: no cover - import-time guard
        counts["errors"].append(f"import: {str(e)[:200]}")
        return counts

    try:
        engine = get_sql_engine()
    except Exception as e:
        counts["errors"].append(f"engine: {str(e)[:200]}")
        return counts

    try:
        with engine.connect() as conn:
            for m in result.measures:
                # dash_rules_db: store DAX as a "measure" rule
                try:
                    conn.execute(text(
                        "INSERT INTO public.dash_rules_db "
                        "(project_slug, name, value, kind) VALUES "
                        "(:slug, :name, :value, 'measure') "
                        "ON CONFLICT DO NOTHING"
                    ), {
                        "slug": project_slug,
                        "name": f"{m.table}.{m.name}",
                        "value": (
                            f"DAX: {m.expression}\n"
                            f"Description: {m.description or ''}"
                        ),
                    })
                    counts["measures_inserted"] += 1
                except Exception as e:
                    counts["errors"].append(f"rules_db: {str(e)[:100]}")

                # dash_company_brain: glossary entry
                try:
                    conn.execute(text(
                        "INSERT INTO public.dash_company_brain "
                        "(project_slug, source_id, name, category, value, scope) "
                        "VALUES "
                        "(:slug, :sid, :name, 'formula', :value, 'project') "
                        "ON CONFLICT DO NOTHING"
                    ), {
                        "slug": project_slug,
                        "sid": source_id,
                        "name": m.name,
                        "value": (
                            f"{m.description or m.name}\n"
                            f"DAX: {m.expression}"
                        ),
                    })
                    counts["brain_entries_inserted"] += 1
                except Exception as e:
                    counts["errors"].append(f"brain: {str(e)[:100]}")
            try:
                conn.commit()
            except Exception as e:
                counts["errors"].append(f"commit: {str(e)[:200]}")
    except Exception as e:
        counts["errors"].append(f"db connect: {str(e)[:200]}")

    return counts
