"""
dash.ingest.content
===================
Utilities for computing content hashes used throughout the ingest pipeline.
"""

import hashlib
from pathlib import Path


def content_hash(data: "bytes | str") -> str:
    """Return the SHA-256 hex digest of *data*.

    Parameters
    ----------
    data:
        - ``bytes`` — hashed directly.
        - ``str``   — treated as a filesystem path; the file is read as bytes
                      before hashing.

    Returns
    -------
    str
        64-character lowercase hexadecimal SHA-256 digest.

    Examples
    --------
    >>> content_hash(b"hello")
    '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'

    >>> content_hash("/tmp/some_file.csv")   # reads the file first
    '<sha256-of-file-contents>'
    """
    if isinstance(data, str):
        data = Path(data).read_bytes()
    return hashlib.sha256(data).hexdigest()
