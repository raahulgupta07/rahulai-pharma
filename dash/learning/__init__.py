"""Self-learning subsystem — autonomous daily growth loop.

This subpackage hosts the components that drive Dash's autonomous
self-learning cycle: curiosity question generation, hypothesis
verification, ledger persistence, and follow-up scheduling.

Concrete imports (CuriosityEngine, HypothesisVerifier, etc.) are
intentionally NOT re-exported here yet — the parallel L5 stream is
authoring shared base/types modules that will be wired in once the
schemas land. Import the submodules directly until then, e.g.::

    from dash.learning.curiosity import CuriosityEngine
"""
from __future__ import annotations

__all__: list[str] = []
