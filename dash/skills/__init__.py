"""Dash-OS Phase 4 — Skills system.

Skills = domain-expert plug-ins. Each skill = name + trigger keywords +
instruction block + optional tool list. Lazy-loaded: agent's `load_skill`
tool detects relevance from user question, fetches skill text + tools,
injects into context for that turn only.

Inspired by Anthropic's skills / demo-os LocalSkills.
"""
