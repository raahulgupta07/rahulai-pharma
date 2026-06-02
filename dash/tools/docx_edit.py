"""DOCX edit tool wrappers — unpack/pack OOXML + inject tracked-change comments.

Wraps dash/skills_cowork/docx/scripts/{unpack,pack,comment}.py via subprocess.
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
def docx_unpack(docx_path: str, output_dir: str = None) -> dict:
    """Unpack DOCX to raw OOXML for low-level edits. Use before docx_add_comment or direct XML surgery."""
    err = _validate_abs(docx_path, "docx_path")
    if err:
        return {"ok": False, "error": err}
    if not os.path.isfile(docx_path):
        return {"ok": False, "error": f"docx_path does not exist: {docx_path}"}
    if output_dir is None:
        ts = time.time_ns()
        output_dir = f"/tmp/docx_unpack_{ts}"
    script = str(_SKILL_BASE / "docx" / "scripts" / "unpack.py")
    cmd = ["python3", script, docx_path, output_dir]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        try:
            file_count = sum(len(files) for _, _, files in os.walk(output_dir))
        except Exception:
            file_count = 0
        return {"ok": True, "output_dir": output_dir, "file_count": file_count}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@tool
def docx_pack(input_dir: str, output_docx: str) -> dict:
    """Repack edited OOXML to .docx. Use after docx_unpack + edits to produce final .docx."""
    err = _validate_abs(input_dir, "input_dir")
    if err:
        return {"ok": False, "error": err}
    err = _validate_abs(output_docx, "output_docx")
    if err:
        return {"ok": False, "error": err}
    if not os.path.isdir(input_dir):
        return {"ok": False, "error": f"input_dir does not exist: {input_dir}"}
    script = str(_SKILL_BASE / "docx" / "scripts" / "pack.py")
    cmd = ["python3", script, input_dir, output_docx]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        try:
            size_bytes = os.path.getsize(output_docx)
        except Exception:
            size_bytes = 0
        return {"ok": True, "output_docx": output_docx, "size_bytes": size_bytes}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@tool
def docx_add_comment(unpacked_dir: str, para_index: int, comment_text: str, parent_id: int = None) -> dict:
    """Inject tracked-change comment into unpacked DOCX (use AFTER docx_unpack). Returns assigned comment_id."""
    err = _validate_abs(unpacked_dir, "unpacked_dir")
    if err:
        return {"ok": False, "error": err}
    if not os.path.isdir(unpacked_dir):
        return {"ok": False, "error": f"unpacked_dir does not exist: {unpacked_dir}"}
    script = str(_SKILL_BASE / "docx" / "scripts" / "comment.py")
    cmd = ["python3", script, unpacked_dir, str(para_index), comment_text]
    if parent_id is not None:
        cmd += ["--parent", str(parent_id)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        try:
            data = json.loads(r.stdout.strip())
            comment_id = data.get("comment_id", data.get("id"))
        except Exception:
            comment_id = None
        return {"ok": True, "comment_id": comment_id}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}
