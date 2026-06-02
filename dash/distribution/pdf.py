"""
PPTX → PDF rendering via LibreOffice headless (soffice).

Returns None + logs warning when soffice is unavailable, so caller can fall
back to attaching the PPTX only.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger("dash.distribution.pdf")


def render_deck_to_pdf(pptx_path: Path) -> Optional[Path]:
    """Convert a PPTX file to PDF via LibreOffice headless.

    Returns the PDF path on success, or None if soffice is not installed
    or the conversion fails.
    """
    pptx_path = Path(pptx_path)
    if not pptx_path.is_file():
        log.warning("render_deck_to_pdf: missing pptx %s", pptx_path)
        return None

    if not shutil.which("soffice"):
        log.warning("soffice not found in PATH — cannot render PDF, caller should fall back to PPTX")
        return None

    out_dir = pptx_path.parent
    try:
        result = subprocess.run(
            [
                "soffice",
                "--headless",
                "--convert-to", "pdf",
                str(pptx_path),
                "--outdir", str(out_dir),
            ],
            capture_output=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        log.warning("soffice timeout converting %s", pptx_path)
        return None
    except Exception as e:
        log.warning("soffice failed for %s: %s", pptx_path, e)
        return None

    if result.returncode != 0:
        log.warning(
            "soffice exit=%s stderr=%s",
            result.returncode,
            (result.stderr or b"").decode("utf-8", errors="replace")[:500],
        )
        return None

    pdf_path = out_dir / (pptx_path.stem + ".pdf")
    if not pdf_path.is_file():
        log.warning("soffice did not produce expected PDF: %s", pdf_path)
        return None

    return pdf_path
