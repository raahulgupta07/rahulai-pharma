"""Ontology public read API — Phase E.

Mirrors the pattern from `dash/embed/` (public-key + Bearer secret). Lets
external integrators query the platform's ontology (types/glossary/links/
lineage) without a session, using a bearer token issued by the super-admin.
"""
from __future__ import annotations
