"""
PPTX edit toolchain — inventory, replace, rearrange, OOXML unpack/pack/validate.
Wraps dash/skills_cowork/pptx/scripts/{inventory,replace,rearrange}.py
and dash/skills_cowork/pptx/ooxml/scripts/{unpack,pack,validate}.py.
Gated by feature_config.tools.office_skills (default OFF).
"""
from agno.tools import tool
import subprocess, json, os, time
from pathlib import Path

_SKILL_BASE = Path(__file__).parent.parent / "skills_cowork"
_PPTX_SCRIPTS = _SKILL_BASE / "pptx" / "scripts"
_OOXML_SCRIPTS = _SKILL_BASE / "pptx" / "ooxml" / "scripts"


def _validate_abs_path(p: str, label: str) -> str | None:
    """Return error string if path is invalid, else None."""
    if not p:
        return f"{label}: path is empty"
    if ".." in Path(p).parts:
        return f"{label}: path traversal not allowed"
    if not os.path.isabs(p):
        return f"{label}: must be an absolute path, got {p!r}"
    return None


def _parse_json_stdout(stdout: str) -> dict | None:
    """Try to parse first JSON object from stdout."""
    if not stdout:
        return None
    try:
        return json.loads(stdout.strip())
    except Exception:
        pass
    # try to find first {...} block
    start = stdout.find("{")
    if start != -1:
        depth, end = 0, -1
        for i, ch in enumerate(stdout[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end != -1:
            try:
                return json.loads(stdout[start:end + 1])
            except Exception:
                pass
    return None


@tool
def pptx_extract_inventory(pptx_path: str) -> dict:
    """Extract editable text structure from existing deck as inventory JSON. Use for 'extract editable text structure from existing deck'."""
    err = _validate_abs_path(pptx_path, "pptx_path")
    if err:
        return {"ok": False, "error": err}
    if not os.path.exists(pptx_path):
        return {"ok": False, "error": f"pptx_path not found: {pptx_path}"}
    ts = time.time_ns()
    out_json = f"/tmp/inv_{ts}.json"
    script = str(_PPTX_SCRIPTS / "inventory.py")
    cmd = ["python3", script, pptx_path, out_json]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        # try stdout JSON first, then output file
        data = _parse_json_stdout(r.stdout)
        if data is None and os.path.exists(out_json):
            with open(out_json) as f:
                data = json.load(f)
        if data is None:
            return {"ok": False, "error": "inventory script produced no JSON output"}
        return {
            "ok": True,
            "inventory": data,
            "slide_count": data.get("slide_count", 0),
            "shape_count": data.get("shape_count", 0),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@tool
def pptx_replace_text(pptx_path: str, replacements: list, output_path: str) -> dict:
    """Apply text replacements to deck via inventory-shaped JSON. Use for 'apply text replacements to deck via inventory-shaped JSON'."""
    for label, p in [("pptx_path", pptx_path), ("output_path", output_path)]:
        err = _validate_abs_path(p, label)
        if err:
            return {"ok": False, "error": err}
    if not os.path.exists(pptx_path):
        return {"ok": False, "error": f"pptx_path not found: {pptx_path}"}
    if not isinstance(replacements, list):
        return {"ok": False, "error": "replacements must be a list of dicts"}
    ts = time.time_ns()
    repl_json = f"/tmp/repl_{ts}.json"
    try:
        with open(repl_json, "w") as f:
            json.dump(replacements, f)
    except Exception as e:
        return {"ok": False, "error": f"failed to write replacements JSON: {e}"}
    script = str(_PPTX_SCRIPTS / "replace.py")
    cmd = ["python3", script, pptx_path, repl_json, output_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        data = _parse_json_stdout(r.stdout) or {}
        return {
            "ok": True,
            "output_path": output_path,
            "replaced_count": data.get("replaced_count", 0),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@tool
def pptx_rearrange_slides(pptx_path: str, slide_indices: str, output_path: str) -> dict:
    """Dedupe/reorder/delete slides in a deck. slide_indices is comma-separated (e.g. '0,34,34,50,52'), duplicates allowed. Use for 'dedupe/reorder/delete slides'."""
    for label, p in [("pptx_path", pptx_path), ("output_path", output_path)]:
        err = _validate_abs_path(p, label)
        if err:
            return {"ok": False, "error": err}
    if not os.path.exists(pptx_path):
        return {"ok": False, "error": f"pptx_path not found: {pptx_path}"}
    if not slide_indices or not slide_indices.strip():
        return {"ok": False, "error": "slide_indices must be a non-empty comma-separated string"}
    script = str(_PPTX_SCRIPTS / "rearrange.py")
    cmd = ["python3", script, pptx_path, output_path, slide_indices]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        data = _parse_json_stdout(r.stdout) or {}
        slide_count = data.get("slide_count", len(slide_indices.split(",")))
        return {
            "ok": True,
            "output_path": output_path,
            "slide_count": slide_count,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@tool
def pptx_ooxml_unpack(pptx_path: str, output_dir: str = "") -> dict:
    """Unpack PPTX to raw OOXML XML for low-level edits. Use for 'unpack PPTX to raw OOXML XML for low-level edits'."""
    err = _validate_abs_path(pptx_path, "pptx_path")
    if err:
        return {"ok": False, "error": err}
    if not os.path.exists(pptx_path):
        return {"ok": False, "error": f"pptx_path not found: {pptx_path}"}
    ts = time.time_ns()
    resolved_dir = output_dir.strip() if output_dir and output_dir.strip() else f"/tmp/ooxml_unpack_{ts}/"
    if ".." in Path(resolved_dir).parts:
        return {"ok": False, "error": "output_dir: path traversal not allowed"}
    script = str(_OOXML_SCRIPTS / "unpack.py")
    cmd = ["python3", script, pptx_path, resolved_dir]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        data = _parse_json_stdout(r.stdout) or {}
        file_count = data.get("file_count", 0)
        if not file_count and os.path.isdir(resolved_dir):
            file_count = sum(1 for _ in Path(resolved_dir).rglob("*") if _.is_file())
        return {
            "ok": True,
            "output_dir": resolved_dir,
            "file_count": file_count,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@tool
def pptx_ooxml_pack(input_dir: str, output_pptx: str) -> dict:
    """Repack edited OOXML directory back to .pptx. Use for 'repack edited OOXML directory back to .pptx'."""
    if not input_dir or not input_dir.strip():
        return {"ok": False, "error": "input_dir is empty"}
    if ".." in Path(input_dir).parts:
        return {"ok": False, "error": "input_dir: path traversal not allowed"}
    err = _validate_abs_path(output_pptx, "output_pptx")
    if err:
        return {"ok": False, "error": err}
    if not os.path.isdir(input_dir):
        return {"ok": False, "error": f"input_dir not found or not a directory: {input_dir}"}
    script = str(_OOXML_SCRIPTS / "pack.py")
    cmd = ["python3", script, input_dir, output_pptx]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        data = _parse_json_stdout(r.stdout) or {}
        size_bytes = data.get("size_bytes", 0)
        if not size_bytes and os.path.exists(output_pptx):
            size_bytes = os.path.getsize(output_pptx)
        return {
            "ok": True,
            "output_pptx": output_pptx,
            "size_bytes": size_bytes,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@tool
def pptx_ooxml_validate(input_dir: str, original_pptx: str = "") -> dict:
    """XSD-validate unpacked OOXML against ISO-IEC29500-4 schemas. Use for 'XSD-validate unpacked OOXML against ISO-IEC29500-4 schemas'."""
    if not input_dir or not input_dir.strip():
        return {"ok": False, "error": "input_dir is empty"}
    if ".." in Path(input_dir).parts:
        return {"ok": False, "error": "input_dir: path traversal not allowed"}
    if not os.path.isdir(input_dir):
        return {"ok": False, "error": f"input_dir not found or not a directory: {input_dir}"}
    script = str(_OOXML_SCRIPTS / "validate.py")
    cmd = ["python3", script, input_dir]
    if original_pptx and original_pptx.strip():
        err = _validate_abs_path(original_pptx.strip(), "original_pptx")
        if err:
            return {"ok": False, "error": err}
        cmd += ["--original", original_pptx.strip()]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        data = _parse_json_stdout(r.stdout) or {}
        if r.returncode != 0 and not data:
            return {"ok": False, "error": (r.stderr or "")[-500:], "stdout": (r.stdout or "")[-200:]}
        return {
            "ok": True,
            "valid": data.get("valid", r.returncode == 0),
            "errors": data.get("errors", []),
            "warnings": data.get("warnings", []),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}
