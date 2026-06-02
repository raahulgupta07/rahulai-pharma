"""deck_visual_qa — Agno tool wrapper for skills_cowork/pptx/scripts/thumbnail.py.

Renders PPTX deck as thumbnail grid JPEGs (LibreOffice + pdftoppm + PIL).
Use before deck delivery for visual QA — feed result JPEGs to Vision LLM.
"""
from __future__ import annotations

import glob
import os
import subprocess
import time
from pathlib import Path

from agno.tools import tool

_SKILL_BASE = Path(__file__).resolve().parent.parent / "skills_cowork"


@tool
def generate_deck_thumbnail_grid(
    pptx_path: str,
    cols: int = 4,
    outline_placeholders: bool = False,
    timeout_s: int = 180,
) -> dict:
    """Render PPTX deck as thumbnail grid JPEGs (LibreOffice + pdftoppm + PIL). Use BEFORE deck delivery for visual QA — feed result JPEGs to Vision LLM to catch layout issues. Pre-warms ~5s on first call (soffice startup)."""
    p = Path(pptx_path)
    if not p.is_absolute():
        return {"ok": False, "error": "pptx_path must be an absolute path"}
    if not pptx_path.endswith(".pptx"):
        return {"ok": False, "error": "pptx_path must end with .pptx"}
    if ".." in pptx_path:
        return {"ok": False, "error": "pptx_path must not contain .."}
    if not p.exists():
        return {"ok": False, "error": f"file not found: {pptx_path}"}

    pid = os.getpid()
    ts = time.time_ns()
    output_prefix = f"/tmp/deck_qa_{pid}_{ts}"

    script = _SKILL_BASE / "pptx" / "scripts" / "thumbnail.py"
    cmd = [
        "python3",
        str(script),
        pptx_path,
        output_prefix,
        "--cols",
        str(cols),
    ]
    if outline_placeholders:
        cmd.append("--outline-placeholders")

    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if r.returncode != 0:
            return {
                "ok": False,
                "error": (r.stderr or "")[-500:],
                "stdout": (r.stdout or "")[-200:],
            }
        jpeg_paths = sorted(glob.glob(f"{output_prefix}*.jpg"))
        return {
            "ok": True,
            "jpeg_paths": jpeg_paths,
            "slide_count": len(jpeg_paths),
            "output_prefix": output_prefix,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}
