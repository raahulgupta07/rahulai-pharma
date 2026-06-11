"""Autonomy heartbeat core.

Token-frugal autonomy: detection is FREE (SQL-only signal reads), thinking is
PAID and rare. A quiet tick = a few SQL reads + sleep, ZERO tokens, no journal
row. Tripped signals dispatch to T3 handlers (currently stubs that journal the
intent only — no LLM / training).
"""
