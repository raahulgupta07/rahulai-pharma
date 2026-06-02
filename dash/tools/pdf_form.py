"""PDF form fill tools — AcroForm field fill + annotation overlay.

Wraps dash/skills_cowork/pdf/scripts/*.py via subprocess.
Gated by feature_config.tools.office_skills (default OFF).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from agno.tools import tool

_SKILL_BASE = Path(__file__).parent.parent / "skills_cowork"


def _validate_abs(p: str, label: str) -> "str | None":
    if not p:
        return f"{label}: path is empty"
    if ".." in p:
        return f"{label}: path traversal not allowed"
    if not os.path.isabs(p):
        return f"{label}: must be an absolute path, got: {p!r}"
    return None


@tool
def pdf_extract_form_fields(pdf_path: str) -> dict:
    """Extract fillable fields from PDF — returns JSON list with field_id/type/page per field. Use before pdf_fill_fillable_fields."""
    err = _validate_abs(pdf_path, "pdf_path")
    if err:
        return {"ok": False, "error": err}
    if not os.path.isfile(pdf_path):
        return {"ok": False, "error": f"pdf_path does not exist: {pdf_path}"}
    ts = time.time_ns()
    out_json = f"/tmp/ff_{ts}.json"
    script = str(_SKILL_BASE / "pdf" / "scripts" / "extract_form_field_info.py")
    cmd = ["python3", script, pdf_path, out_json]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        try:
            with open(out_json, "r") as f:
                fields = json.load(f)
        except Exception as e:
            return {"ok": False, "error": f"failed to read output JSON: {e}"}
        return {"ok": True, "fields": fields}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@tool
def pdf_check_has_fillable(pdf_path: str) -> dict:
    """Quick check whether PDF has AcroForm fields. Returns has_fillable bool. Use before pdf_fill_fillable_fields to verify form is fillable."""
    err = _validate_abs(pdf_path, "pdf_path")
    if err:
        return {"ok": False, "error": err}
    if not os.path.isfile(pdf_path):
        return {"ok": False, "error": f"pdf_path does not exist: {pdf_path}"}
    script = str(_SKILL_BASE / "pdf" / "scripts" / "check_fillable_fields.py")
    cmd = ["python3", script, pdf_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        stdout = (r.stdout or "").strip().lower()
        has_fillable = stdout in ("true", "1", "yes")
        return {"ok": True, "has_fillable": has_fillable}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@tool
def pdf_fill_fillable_fields(pdf_path: str, field_values: dict, output_pdf: str) -> dict:
    """Fill PDF AcroForm fields. field_values = {field_id: value}. Use pdf_extract_form_fields first to get field IDs. Writes filled PDF to output_pdf."""
    err = _validate_abs(pdf_path, "pdf_path")
    if err:
        return {"ok": False, "error": err}
    err = _validate_abs(output_pdf, "output_pdf")
    if err:
        return {"ok": False, "error": err}
    if not os.path.isfile(pdf_path):
        return {"ok": False, "error": f"pdf_path does not exist: {pdf_path}"}
    ts = time.time_ns()
    vals_json = f"/tmp/vals_{ts}.json"
    # Anthropic script expects list shape: [{field_id, page, value}]. Auto-resolve page via extract.
    page_map: dict = {}
    try:
        extract_script = str(_SKILL_BASE / "pdf" / "scripts" / "extract_form_field_info.py")
        info_json = f"/tmp/ff_info_{time.time_ns()}.json"
        ex_r = subprocess.run(
            ["python3", extract_script, pdf_path, info_json],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if ex_r.returncode == 0 and os.path.exists(info_json):
            with open(info_json) as f:
                info = json.load(f)
            for fld in (info.get("fields") if isinstance(info, dict) else info) or []:
                if isinstance(fld, dict) and "field_id" in fld:
                    page_map[fld["field_id"]] = fld.get("page", 1)
    except Exception:
        pass

    if isinstance(field_values, dict):
        fields_list = [
            {"field_id": k, "page": page_map.get(k, 1), "value": v}
            for k, v in field_values.items()
        ]
    elif isinstance(field_values, list):
        fields_list = []
        for item in field_values:
            if isinstance(item, dict):
                d = dict(item)
                d.setdefault("page", page_map.get(d.get("field_id"), 1))
                fields_list.append(d)
    else:
        return {"ok": False, "error": "field_values must be dict {id:value} or list [{field_id,value}]"}
    try:
        with open(vals_json, "w") as f:
            json.dump(fields_list, f)
    except Exception as e:
        return {"ok": False, "error": f"failed to write values JSON: {e}"}
    script = str(_SKILL_BASE / "pdf" / "scripts" / "fill_fillable_fields.py")
    cmd = ["python3", script, pdf_path, vals_json, output_pdf]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        fields_filled = 0
        validation_errors = []
        try:
            data = json.loads(r.stdout.strip())
            fields_filled = data.get("fields_filled", 0)
            validation_errors = data.get("validation_errors", [])
        except Exception:
            pass
        return {
            "ok": True,
            "output_pdf": output_pdf,
            "fields_filled": fields_filled,
            "validation_errors": validation_errors,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@tool
def pdf_fill_with_annotations(pdf_path: str, annotations: list, output_pdf: str) -> dict:
    """Overlay text/checkmarks on non-fillable PDFs via canvas merge. Use when PDF has no AcroForm fields. Each annotation: {page, bbox: [x1,y1,x2,y2], type: 'text'|'checkmark', value: str}."""
    err = _validate_abs(pdf_path, "pdf_path")
    if err:
        return {"ok": False, "error": err}
    err = _validate_abs(output_pdf, "output_pdf")
    if err:
        return {"ok": False, "error": err}
    if not os.path.isfile(pdf_path):
        return {"ok": False, "error": f"pdf_path does not exist: {pdf_path}"}
    ts = time.time_ns()
    ann_json = f"/tmp/ann_{ts}.json"
    try:
        with open(ann_json, "w") as f:
            json.dump(annotations, f)
    except Exception as e:
        return {"ok": False, "error": f"failed to write annotations JSON: {e}"}
    script = str(_SKILL_BASE / "pdf" / "scripts" / "fill_pdf_form_with_annotations.py")
    cmd = ["python3", script, pdf_path, ann_json, output_pdf]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        return {"ok": True, "output_pdf": output_pdf}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}
