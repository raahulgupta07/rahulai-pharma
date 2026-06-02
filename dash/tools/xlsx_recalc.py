"""xlsx_recalc — Agno tool wrapper for skills_cowork/xlsx/recalc.py.

Recalculates Excel formula values via LibreOffice headless, replacing
openpyxl-written formula strings with live computed values.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agno.tools import tool

_SKILL_BASE = Path(__file__).resolve().parent.parent / "skills_cowork"


@tool
def xlsx_recalc(xlsx_path: str, timeout_s: int = 60) -> dict:
    """Recalculate Excel formulas via LibreOffice. Use AFTER generating XLSX with openpyxl (which writes formula strings but NO values — users see blanks in Excel). Returns errors found (#REF, #DIV/0, #VALUE, #NAME, #N/A)."""
    p = Path(xlsx_path)
    if not p.is_absolute():
        return {"ok": False, "error": "xlsx_path must be an absolute path"}
    if not xlsx_path.endswith(".xlsx"):
        return {"ok": False, "error": "xlsx_path must end with .xlsx"}
    if ".." in xlsx_path:
        return {"ok": False, "error": "xlsx_path must not contain .."}
    if not p.exists():
        return {"ok": False, "error": f"file not found: {xlsx_path}"}

    script = _SKILL_BASE / "xlsx" / "recalc.py"
    cmd = ["python3", str(script), xlsx_path, str(timeout_s)]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s + 10,
            check=False,
        )
        if r.returncode != 0:
            return {
                "ok": False,
                "error": (r.stderr or "")[-500:],
                "stdout": (r.stdout or "")[-200:],
            }
        stdout = (r.stdout or "").strip()
        if stdout:
            try:
                data = json.loads(stdout)
                errors = data.get("errors", [])
                return {
                    "ok": True,
                    "status": data.get("status", "ok"),
                    "errors_found": len(errors),
                    "errors": errors,
                }
            except json.JSONDecodeError:
                pass
        return {"ok": True, "status": "ok", "errors_found": 0, "errors": []}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}
