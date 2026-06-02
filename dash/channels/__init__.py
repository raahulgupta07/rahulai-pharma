"""Dash-OS Phase 5 — Comm surfaces: Slack, Email, Voice.

Each module exposes: handle_inbound(payload) → agent response,
send_outbound(thread_id, text) → external delivery.

Threads preserve dash session_id so multi-turn context carries between
external channel and Dash chat.
"""
