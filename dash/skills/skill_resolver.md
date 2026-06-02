---
name: skill_resolver
description: Pick the best skill to handle a user query. Returns chosen skill name + reason.
tags: [meta, routing, internal]
---

# skill_resolver

Use when uncertain which skill applies. Calls `dash.skills.resolver.resolve()`.

Returns: `{chosen, candidates, reason}`

LLM intent classification — replaces word-overlap registry matching.
Falls back to `registry.find_skills_for` top-1 when LLM unavailable.
