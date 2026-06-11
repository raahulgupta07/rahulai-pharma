"""Public embed endpoints — Phase 2.

Endpoints called by the embed widget itself (browsers on external sites).
No auth header required; security comes from (embed_id + public_key) and
either an allowlisted Origin (auth_mode=public) or HMAC over the user
payload (auth_mode=hmac).

This file is intentionally separate from `app/embed.py` (Phase 1, admin
CRUD) which requires authenticated dashboard users.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

import os as _os


def _embed_log_bodies() -> bool:
    """True when EMBED_LOG_BODIES is enabled — store raw question/answer text on
    dash_embed_calls (message_text/response_text). Off by default (privacy + size)."""
    return _os.getenv("EMBED_LOG_BODIES", "0").strip().lower() in ("1", "true", "yes", "on")


def _embed_call_insert_sql(log_bodies: bool) -> str:
    """Build the dash_embed_calls INSERT. Adds body columns only when logging is on
    (and migration 177 has added them), so older DBs / bodies-off keep working.
    Always writes token/cost columns (migration 178) for the Usage dashboard."""
    cols = ("embed_id, session_token, external_user, origin, ip, "
            "message_chars, response_chars, latency_ms, success, error, "
            "tokens_in, tokens_out, cost_usd, engine_model")
    vals = (":e, :t, :u, :o, :ip, :mc, :rc, :ms, :s, :err, "
            ":ti, :to, :cost, :emodel")
    if log_bodies:
        cols += ", message_text, response_text"
        vals += ", :mt, :rt"
    return f"INSERT INTO public.dash_embed_calls ({cols}) VALUES ({vals})"


def _embed_usage_from_response(response) -> dict:
    """Pull token usage + model from an Agno RunResponse for the Usage dashboard.
    Reuses RunMetrics (input_tokens/output_tokens). Fail-soft → zeros."""
    out = {"tokens_in": 0, "tokens_out": 0, "model": ""}
    try:
        m = getattr(response, "metrics", None)
        if hasattr(m, "to_dict"):
            m = m.to_dict()
        if isinstance(m, dict):
            ti = m.get("input_tokens"); to = m.get("output_tokens")
            # metrics values may be per-message lists — sum if so
            out["tokens_in"] = int(sum(ti) if isinstance(ti, (list, tuple)) else (ti or 0))
            out["tokens_out"] = int(sum(to) if isinstance(to, (list, tuple)) else (to or 0))
        mdl = getattr(response, "model", None) or (m.get("model") if isinstance(m, dict) else None)
        if mdl:
            out["model"] = str(mdl)
    except Exception:
        pass
    return out

router = APIRouter(prefix="/api/embed", tags=["EmbedPublic"])


_WIDGET_PATH = "/app/dash/embed/widget.js"
_WIDGET_CACHE: tuple[str, float] | None = None  # (content, mtime)


# ── Consumer-mode response sanitizer ──────────────────────────────────────────
import re as _re

# Any "[TAG:...]" style structured marker that consumer users shouldn't see.
_CONSUMER_TAG_RE = _re.compile(r"\[[A-Za-z_][A-Za-z0-9_]*:[^\]]*\]")
_CODE_BLOCK_RE = _re.compile(r"```.*?```", _re.DOTALL)
_HTML_CODE_RE = _re.compile(r"</?code[^>]*>", _re.IGNORECASE)
_MD_TABLE_SEP_RE = _re.compile(r"^\s*\|?\s*:?-{3,}.*$", _re.MULTILINE)
# Lines starting with an agent name + colon, e.g. "Analyst: ..."
_AGENT_PREFIX_RE = _re.compile(
    r"^(?:Analyst|Engineer|Researcher|Data Scientist|Leader|Customer Strategist|Router|Visualizer|Inspector|Conductor|Scanner|Parser|Judge|Comparator|Diagnostician|Narrator|Validator|Planner)\s*:.*$",
    _re.MULTILINE,
)
_ROUTING_LINE_RE = _re.compile(r"^\s*(?:\[ROUTING|FAST mode|DEEP mode).*$", _re.MULTILINE | _re.IGNORECASE)
# A trailing price fragment whose value was masked for the consumer, e.g.
# "… (1.16kg) — [banded] MMK" / ": [banded] Ks". Showing a banded price is pure
# noise to an end-user — strip the dangling "— [banded] <currency>" tail so the
# product line reads cleanly. Keeps everything before the separator.
_BANDED_PRICE_TAIL_RE = _re.compile(
    r"\s*[—–\-:]\s*\[banded\]\s*(?:MMK|Ks?|Kyats?|USD|\$)?\s*$",
    _re.IGNORECASE | _re.MULTILINE,
)
# A bare dangling list marker left after truncation ("* …" / "- " / "1.").
_DANGLING_BULLET_RE = _re.compile(r"(?:\n|^)\s*(?:[-*•]|\d+[.)])\s*[…]*\s*$")


def _smart_truncate(out: str, max_chars: int) -> tuple[str, bool]:
    """Truncate on a line/sentence boundary so we never cut mid-bullet and
    never leave a dangling list marker. Returns (text, was_truncated)."""
    if not max_chars or len(out) <= max_chars:
        return out, False
    window = out[:max_chars]
    # Prefer the last full line; fall back to last sentence end.
    cut = window.rfind("\n")
    if cut < int(max_chars * 0.5):
        m = list(_re.finditer(r"[.!?။]\s", window))
        cut = m[-1].end() if m else -1
    trimmed = (window[:cut] if cut > 0 else window).rstrip()
    trimmed = _DANGLING_BULLET_RE.sub("", trimmed).rstrip()
    return (trimmed + " …"), True


# Follow-up suggestion twins — EN + Burmese (MY) so the chips mirror the
# conversation language. Keyed by the heuristic branch.
_FOLLOWUPS = {
    "price": {
        "en": ["Which of these are in stock?",
               "Show me cheaper alternatives",
               "What are substitutes for the first one?"],
        "my": ["ဒီထဲက ဘယ်ဟာတွေ စတော့ရှိလဲ?",
               "ဈေးသက်သာတဲ့ အစားထိုးဆေးတွေ ပြပါ",
               "ပထမတစ်ခုအတွက် အစားထိုးဆေးတွေ ဘာတွေလဲ?"],
    },
    "stock": {
        "en": ["Which branch has the most?",
               "Show me what's running low",
               "Suggest substitutes for out-of-stock items"],
        "my": ["ဘယ်ဆိုင်မှာ အများဆုံးရှိလဲ?",
               "လက်ကျန်နည်းနေတဲ့ ဆေးတွေ ပြပါ",
               "ပြတ်နေတဲ့ ဆေးတွေအတွက် အစားထိုးဆေး အကြံပြုပါ"],
    },
    "substitute": {
        "en": ["Which substitutes are in stock?",
               "Compare their prices",
               "What is this used for?"],
        "my": ["ဘယ်အစားထိုးဆေးတွေ စတော့ရှိလဲ?",
               "ဈေးနှုန်းတွေ နှိုင်းယှဉ်ပြပါ",
               "ဒါက ဘာအတွက် သုံးတာလဲ?"],
    },
    "indication": {
        "en": ["What are the alternatives?",
               "Any interactions to watch for?",
               "Which of these do you stock?"],
        "my": ["အစားထိုးဆေးတွေက ဘာတွေလဲ?",
               "သတိထားရမယ့် ဆေးတွဲဖက်မှုများ ရှိလား?",
               "ဒီထဲက ဘယ်ဟာတွေ သင့်ဆိုင်မှာ ရှိလဲ?"],
    },
    "default": {
        "en": ["Show me the top products in this category",
               "What's in stock right now?",
               "Find substitutes for a product"],
        "my": ["ဒီအမျိုးအစားထဲက ထိပ်တန်းဆေးတွေ ပြပါ",
               "အခု ဘာတွေ စတော့ရှိလဲ?",
               "ဆေးတစ်ခုအတွက် အစားထိုးဆေး ရှာပါ"],
    },
}


def _is_burmese(s: str) -> bool:
    """True if the text contains Burmese-script characters (U+1000–U+109F)."""
    return any("က" <= c <= "႟" for c in (s or ""))


def _consumer_followups(question: str, answer: str, max_n: int = 3) -> list[str]:
    """Cheap, no-LLM contextual follow-up suggestions for the embed widget.
    Heuristic on the question/answer shape — keeps latency at zero. Chips are
    returned in Burmese when the question is Burmese, else English."""
    q = (question or "").lower()
    a = (answer or "").lower()
    listy = bool(_re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", answer or "")) or "top " in q
    # Burmese has no English keywords → fall back to the question's own intent
    # words where possible, else 'default'. Branch detection stays EN-keyword
    # based (works for EN); Burmese questions mostly land on 'default'/'stock'.
    if any(w in q for w in ("expensive", "cheapest", "price", "cost", "top ")) and listy:
        branch = "price"
    elif any(w in q for w in ("stock", "available", "inventory", "shelf")) \
            or any(w in question for w in ("ရှိ", "စတော့", "လက်ကျန်")):
        branch = "stock"
    elif any(w in q for w in ("substitute", "alternative", "instead of")) \
            or "အစားထိုး" in question:
        branch = "substitute"
    elif any(w in (q + a) for w in ("indication", "symptom", "used for", "treat", "fever", "pain")):
        branch = "indication"
    else:
        branch = "default"
    lang = "my" if _is_burmese(question) else "en"
    out = _FOLLOWUPS[branch][lang]
    # de-dupe, cap
    seen: set[str] = set()
    uniq = [x for x in out if not (x in seen or seen.add(x))]
    return uniq[:max_n]


def sanitize_consumer_response(text: str, max_chars: int = 600) -> str:
    """Strip developer-facing artifacts from an agent reply destined for an
    end-user widget. Removes structured tags, code blocks, agent-routing chatter,
    markdown-table separators, banded-price tails, and truncates to max_chars on
    a clean boundary."""
    if not text:
        return ""
    out = text

    # 1. Structured tags like [KPI:...], [CONFIDENCE:...], [TOOL:...], etc.
    out = _CONSUMER_TAG_RE.sub("", out)

    # 2. Triple-backtick code blocks.
    out = _CODE_BLOCK_RE.sub("", out)

    # 3. HTML <code> wrappers (leave the inner text).
    out = _HTML_CODE_RE.sub("", out)

    # 4. Agent-prefix lines and routing/mode lines.
    out = _AGENT_PREFIX_RE.sub("", out)
    out = _ROUTING_LINE_RE.sub("", out)

    # 5. Markdown table separator rows (|---|---|). The rows themselves
    #    survive; only the separator is noisy.
    out = _MD_TABLE_SEP_RE.sub("", out)

    # 5b. Banded-price tails ("— [banded] MMK") — noise to the end-user.
    out = _BANDED_PRICE_TAIL_RE.sub("", out)

    # 6. Collapse runs of blank lines + strip leading/trailing whitespace lines.
    lines = [ln.rstrip() for ln in out.splitlines()]
    cleaned: list[str] = []
    prev_blank = False
    for ln in lines:
        is_blank = not ln.strip()
        if is_blank and prev_blank:
            continue
        cleaned.append(ln)
        prev_blank = is_blank
    out = "\n".join(cleaned).strip()

    # 7. Cap length on a clean boundary (never mid-bullet).
    out, _ = _smart_truncate(out, max_chars)
    return out


def _sanitize_fragment(text: str) -> str:
    """Strip developer-facing artifacts from a COMMITTED prefix of the running
    answer (the incremental consumer streamer). Applies the SAME removal rules as
    `sanitize_consumer_response` (tags / code blocks / <code> / agent-prefix /
    routing / md-table-separator / banded-price tail) but does NOT truncate and
    does NOT `.strip()` the result — stripping would shift the emitted_len cursor
    and risk re-emitting or skipping characters. Internal blank-line collapsing is
    likewise skipped so the committed text stays a stable, append-only prefix.

    SECURITY: only ever called on a prefix that the hold-window guarantee has
    proven contains no half-open sensitive token (see `_consumer_hold_len`)."""
    if not text:
        return ""
    out = text
    out = _CONSUMER_TAG_RE.sub("", out)        # [TAG:...]
    out = _CODE_BLOCK_RE.sub("", out)          # ```...```
    out = _HTML_CODE_RE.sub("", out)           # <code>
    out = _AGENT_PREFIX_RE.sub("", out)        # "Analyst: ..."
    out = _ROUTING_LINE_RE.sub("", out)        # ROUTING / FAST mode
    out = _MD_TABLE_SEP_RE.sub("", out)        # |---|---|
    out = _BANDED_PRICE_TAIL_RE.sub("", out)   # — [banded] MMK
    return out


def _consumer_hold_len(raw: str) -> int:
    """Return how many characters at the TAIL of `raw` must be HELD BACK (not yet
    committed) because they could be the beginning of an unclosed sensitive token
    that `_sanitize_fragment` would otherwise fail to strip if cut mid-token.

    The hold-window invariant: `raw[:len(raw) - hold]` is SAFE to sanitize +
    commit, because no sensitive construct can begin inside that prefix and remain
    unclosed past it. We hold from the EARLIEST suspicious open position to the end
    of the string. Conservative by design — when in doubt, hold (correctness of
    masking > stream smoothness). Cases held:

      • an open `[` with no later `]`  → could become `[TAG:...]`
      • an odd number of  ```  fences  → inside an open code block
      • an open `<` that could grow into `<code` / `</code` (`<`, `<c`, `<co`,
        `<cod`, `<code` …, or `</`, `</c` …) with no closing `>`
      • a trailing partial line that *starts like* a markdown table separator
        (`|`, `|-`, `:--` …) and has no terminating newline yet
    """
    if not raw:
        return 0
    n = len(raw)
    hold_from = n  # earliest byte index we must hold from (default: hold nothing)

    # 1. Unclosed '[' — a [TAG:...] could be mid-arrival.
    lb = raw.rfind("[")
    if lb != -1 and "]" not in raw[lb:]:
        hold_from = min(hold_from, lb)

    # 2. Odd number of ``` fences → we are inside an open code block. Hold from
    #    the LAST opening fence so the whole open block stays masked.
    if raw.count("```") % 2 == 1:
        last_fence = raw.rfind("```")
        if last_fence != -1:
            hold_from = min(hold_from, last_fence)

    # 3. Unclosed '<' that could be an emerging <code>/</code> tag. Hold from the
    #    last '<' when there's no '>' after it AND the partial matches a code-tag
    #    prefix (so we don't needlessly hold ordinary '<' like "a < b").
    lt = raw.rfind("<")
    if lt != -1 and ">" not in raw[lt:]:
        partial = raw[lt:].lower()
        _code_prefixes = ("</code", "<code", "</cod", "<cod", "</co", "<co",
                          "</c", "<c", "</", "<")
        if any(partial == p[:len(partial)] for p in _code_prefixes):
            hold_from = min(hold_from, lt)

    # 4. Trailing partial markdown table-separator row with no closing newline.
    #    e.g. "...\n| --- | ---" still streaming → hold the whole partial line so
    #    `_MD_TABLE_SEP_RE` (anchored ^...$) can match it once the newline lands.
    nl = raw.rfind("\n")
    tail = raw[nl + 1:] if nl != -1 else raw
    bare = tail.lstrip()
    if bare and all(c in "|:- " for c in bare) and ("-" in bare or "|" in bare):
        line_start = nl + 1 if nl != -1 else 0
        hold_from = min(hold_from, line_start)

    return n - hold_from


@router.get("/docs")
def serve_embed_docs():
    """Self-contained docs page for integrators."""
    from fastapi.responses import HTMLResponse
    html = """<!doctype html>
<html><head><meta charset="utf-8"/>
<title>Dash Agent Embed — Integration Docs</title>
<style>
  body { font-family: ui-monospace, "Berkeley Mono", Menlo, monospace; background: #0a0a0a; color: #e5e5e0; max-width: 880px; margin: 0 auto; padding: 30px 20px; line-height: 1.6; }
  h1 { color: #00fc40; font-size: 22px; }
  h2 { color: #66aaff; font-size: 14px; margin-top: 28px; padding-top: 14px; border-top: 1px solid #1a1a1a; }
  h3 { color: #ff9d00; font-size: 12px; margin-top: 18px; }
  code { background: #1a1a1a; padding: 1px 6px; color: #00fc40; font-size: 12px; }
  pre { background: #1a1a1a; padding: 14px; overflow-x: auto; font-size: 11px; line-height: 1.6; border-left: 2px solid #333; }
  pre code { background: transparent; padding: 0; color: #e5e5e0; }
  a { color: #66aaff; }
  table { border-collapse: collapse; width: 100%; font-size: 12px; margin: 10px 0; }
  th, td { border-bottom: 1px solid #1a1a1a; padding: 6px 10px; text-align: left; }
  th { color: #888; font-weight: 700; }
  .note { background: #1a1a0d; border-left: 2px solid #ff9d00; padding: 10px 14px; margin: 14px 0; font-size: 12px; }
</style>
</head><body>

<h1>◉ Dash Agent — Embed Integration</h1>
<p style="color:#888; font-size:12px;">Drop a single &lt;script&gt; tag into your site to load the agent as a chat widget.</p>

<h2>1. Quick start (public mode)</h2>
<p>For marketing sites / docs / anonymous chat. No user identity required.</p>
<pre><code>&lt;script src="HOST/api/embed/widget.js"
        data-embed-id="emb_xxx"
        data-key="pub_xxx"
        async&gt;&lt;/script&gt;</code></pre>
<p>Replace <code>HOST</code>, <code>emb_xxx</code>, <code>pub_xxx</code> with values from your project Settings → EMBED → CREATE.</p>

<h2>2. With user identity (HMAC mode)</h2>
<p>For logged-in apps. Host server signs the user payload with the embed's <strong>secret_key</strong>; the widget passes payload + signature to Dash; Dash verifies → trusts the user identity for row-level filtering.</p>

<h3>Server-side signing</h3>
<pre><code># Python
import hmac, hashlib, json
EMBED_SECRET = "sk_xxx"   # secret_key from Dash, store in env

payload = {"id": user.id, "store_id": user.store_id}
canon = json.dumps(payload, sort_keys=True, separators=(",",":"))
sig = hmac.new(EMBED_SECRET.encode(), canon.encode(), hashlib.sha256).hexdigest()</code></pre>

<pre><code>// Node
const crypto = require('crypto');
const canon = JSON.stringify(payload, Object.keys(payload).sort());
const sig = crypto.createHmac('sha256', SECRET).update(canon).digest('hex');</code></pre>

<pre><code>// PHP
$canon = json_encode($payload, JSON_UNESCAPED_SLASHES);
$sig = hash_hmac('sha256', $canon, $EMBED_SECRET);</code></pre>

<h3>Render in template</h3>
<pre><code>&lt;script src="HOST/api/embed/widget.js"
        data-embed-id="emb_xxx"
        data-key="pub_xxx"
        data-user='{{ canon | safe }}'
        data-user-sig="{{ sig }}"
        async&gt;&lt;/script&gt;</code></pre>

<h2>3. Configuration attributes</h2>
<table>
<tr><th>Attribute</th><th>Required</th><th>Description</th></tr>
<tr><td><code>data-embed-id</code></td><td>yes</td><td>Public embed identifier</td></tr>
<tr><td><code>data-key</code></td><td>yes</td><td>Public key (browser-safe)</td></tr>
<tr><td><code>data-user</code></td><td>HMAC mode</td><td>Canonical-JSON user payload</td></tr>
<tr><td><code>data-user-sig</code></td><td>HMAC mode</td><td>HMAC-SHA256 hex signature</td></tr>
<tr><td><code>data-position</code></td><td>no</td><td>bottom-right (default), bottom-left, top-right, top-left</td></tr>
<tr><td><code>data-theme</code></td><td>no</td><td>dark (default) or light</td></tr>
<tr><td><code>data-greeting</code></td><td>no</td><td>First message shown in panel</td></tr>
<tr><td><code>data-title</code></td><td>no</td><td>Title in widget header</td></tr>
</table>

<h2>4. Security model</h2>
<table>
<tr><th>Layer</th><th>What it does</th></tr>
<tr><td>Origin allowlist</td><td>Server checks <code>Origin</code> header against embed config. Off-list → 403.</td></tr>
<tr><td>Sec-Fetch-Site</td><td>Blocks direct curl/address-bar requests for /embed/* endpoints.</td></tr>
<tr><td>HMAC user verify</td><td>Server recomputes HMAC, rejects mismatched signatures.</td></tr>
<tr><td>Rate limit</td><td>Per-embed sliding 60s window, default 30/min.</td></tr>
<tr><td>Session TTL</td><td>15 minutes; auto-refreshed by widget before expiry.</td></tr>
<tr><td>Per-embed CORS</td><td>Only echoes Origin if it matches allowed_origins.</td></tr>
<tr><td>Shadow DOM</td><td>Widget styles isolated from host page CSS.</td></tr>
<tr><td>Audit log</td><td>Every chat call logged with latency + status.</td></tr>
</table>

<div class="note">
  <strong>⚠ Never expose secret_key to the browser.</strong> It is server-only. If accidentally leaked,
  click ROTATE in EMBED settings to invalidate it instantly.
</div>

<h2>5. Programmatic API</h2>
<p>The widget exposes a small JS API for testing:</p>
<pre><code>DashAgent.open();             // open the panel
DashAgent.close();            // close it
DashAgent.send("hello");      // send a message programmatically
DashAgent.config;             // {embedId, apiOrigin, theme}</code></pre>

<h2>6. REST endpoints (for your own clients)</h2>
<table>
<tr><th>Method</th><th>Path</th><th>Purpose</th></tr>
<tr><td>GET</td><td><code>/api/embed/widget.js</code></td><td>Widget JavaScript</td></tr>
<tr><td>POST</td><td><code>/api/embed/session/create</code></td><td>Bootstrap session token</td></tr>
<tr><td>POST</td><td><code>/api/embed/chat</code></td><td>Send message, get reply</td></tr>
</table>

<h3>POST /api/embed/session/create</h3>
<pre><code>{
  "embed_id":   "emb_xxx",
  "public_key": "pub_xxx",
  "user":       {"id":"alice","store_id":"MUM01"},   // optional
  "signature":  "abc123..."                          // HMAC-SHA256 hex, required if HMAC mode
}
→ 200 {"session_token":"sess_xxx","expires_in":900,"feature_config":{}}
→ 403 {"detail":"origin not allowed" | "invalid user signature" | "embed disabled"}</code></pre>

<h3>POST /api/embed/chat</h3>
<pre><code>{ "session_token": "sess_xxx", "message": "what is X?" }
→ 200 {"content":"...","session_token":"sess_xxx","external_user":"alice","latency_ms":1234}
→ 401 session expired
→ 429 rate limit
→ 403 embed disabled</code></pre>

<h2>7. Troubleshooting</h2>
<table>
<tr><th>Symptom</th><th>Cause + fix</th></tr>
<tr><td>403 origin not allowed</td><td>Add the host's exact origin (scheme + host + port, no path) to <code>allowed_origins</code></td></tr>
<tr><td>403 invalid user signature</td><td>Canonical JSON differs. Always sort keys, no spaces. Re-check your HMAC code matches the snippet in Dash UI.</td></tr>
<tr><td>429 rate limit exceeded</td><td>Increase <code>rate_limit_per_min</code> on the embed config</td></tr>
<tr><td>Widget bubble doesn't appear</td><td>Check browser console — script may have failed to load. Verify CORS / network tab.</td></tr>
<tr><td>"agent unavailable"</td><td>Embed disabled by admin. Re-enable in Dash UI.</td></tr>
</table>

<p style="margin-top:30px; color:#666; font-size:11px; text-align:center;">
  Manage your embeds in Dash → project → Settings → EMBED tab.
</p>
</body></html>"""
    return HTMLResponse(html, headers={
        "Cache-Control": "public, max-age=600",
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/widget.js")
def serve_widget_js():
    """Serve the embed widget JavaScript with permissive CORS + browser cache."""
    import os
    from fastapi.responses import Response

    global _WIDGET_CACHE
    try:
        st = os.stat(_WIDGET_PATH)
        if not _WIDGET_CACHE or _WIDGET_CACHE[1] != st.st_mtime:
            with open(_WIDGET_PATH, "r", encoding="utf-8") as f:
                _WIDGET_CACHE = (f.read(), st.st_mtime)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="widget not deployed")

    return Response(
        content=_WIDGET_CACHE[0],
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=300",      # 5min cache
            "Access-Control-Allow-Origin": "*",          # widget is meant to load anywhere
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ── Developer SDK download ────────────────────────────────────────────────────
# Ready-to-paste client code shown in the admin "Snippet & Docs" tab. Each file
# is served raw (copy/download) and as a bundle (.zip). Placeholders are
# templated with the caller's real embed_id / public_key / base_url so the
# downloaded code runs as-is.
_SDK_DIR = "/app/examples"
_SDK_FILES = [
    ("widget-embed.php",     "php",  "PHP page — drop-in chat bubble, user-scoped (HMAC). Fastest path."),
    ("CityAgentClient.php",  "php",  "PHP SDK class — your own UI / server-to-server. No Composer."),
    ("rest_client.py",       "python", "Python SDK — stdlib only, no pip."),
    ("rest_client.js",       "javascript", "Node 18+ SDK — zero deps."),
    ("quickstart.sh",        "bash", "Bash + curl — 10-second end-to-end smoke test."),
    ("README.md",            "markdown", "Integration guide — 3 paths, auth modes, error table."),
]
# placeholder values inside the example files, replaced at download time
_SDK_PLACEHOLDERS = {
    "base":    "http://localhost:8011",
    "embed":   "emb_rGd8VWW8DloS6WNNssvenA",
    "pubkey":  "pub_FWWyXah2Sv0iuN5f8TwQQH1v2LaoeIUT",
}


def _public_base(req: "Request | None" = None) -> str | None:
    """Canonical public origin for snippet/SDK URLs.

    Priority: PUBLIC_URL / WEBUI_URL env (set this to the AWS domain) →
    request Origin/Referer → request base_url. Returns None if nothing usable,
    in which case the localhost placeholder is left untouched.
    """
    import os
    env = (os.getenv("PUBLIC_URL") or os.getenv("WEBUI_URL") or "").rstrip("/")
    if env:
        return env
    if req is not None:
        o = _extract_origin(req)
        if o:
            return o.rstrip("/")
        try:
            return str(req.base_url).rstrip("/")
        except Exception:
            return None
    return None


def _sdk_read(name: str, *, base: str | None = None, embed: str | None = None,
              pubkey: str | None = None) -> str:
    import os
    if name not in {f[0] for f in _SDK_FILES}:
        raise HTTPException(status_code=404, detail="unknown sdk file")
    path = os.path.join(_SDK_DIR, name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="sdk file not deployed")
    if base:
        content = content.replace(_SDK_PLACEHOLDERS["base"], base.rstrip("/"))
    if embed:
        content = content.replace(_SDK_PLACEHOLDERS["embed"], embed)
    if pubkey:
        content = content.replace(_SDK_PLACEHOLDERS["pubkey"], pubkey)
    return content


@router.get("/sdk")
def list_sdk_files():
    """Manifest of downloadable client SDK files for the admin Snippet & Docs UI."""
    import os
    out = []
    for name, lang, desc in _SDK_FILES:
        try:
            size = os.path.getsize(os.path.join(_SDK_DIR, name))
        except OSError:
            size = 0
        out.append({"name": name, "lang": lang, "desc": desc, "size": size})
    return {"files": out}


@router.get("/sdk/file/{name}")
def get_sdk_file(name: str, request: Request):
    """Raw SDK file, placeholders templated with caller's embed values.

    Query params (all optional): base, embed, pubkey. ?download=1 forces a
    file-download disposition; otherwise served inline for in-UI preview.
    """
    from fastapi.responses import Response
    q = request.query_params
    content = _sdk_read(name, base=q.get("base") or _public_base(request),
                        embed=q.get("embed"), pubkey=q.get("pubkey"))
    headers = {"Cache-Control": "no-store"}
    if q.get("download"):
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return Response(content=content, media_type="text/plain; charset=utf-8", headers=headers)


@router.get("/sdk.zip")
def download_sdk_zip(request: Request):
    """All SDK files as a single .zip, placeholders templated for the caller."""
    import io
    import zipfile
    from fastapi.responses import Response

    q = request.query_params
    base = q.get("base") or _public_base(request)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, _lang, _desc in _SDK_FILES:
            try:
                content = _sdk_read(name, base=base, embed=q.get("embed"), pubkey=q.get("pubkey"))
            except HTTPException:
                continue  # skip files not deployed
            zf.writestr(f"cityagent-sdk/{name}", content)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="cityagent-sdk.zip"',
            "Cache-Control": "no-store",
        },
    )


# Gateway multi-shop bundle: ONE key-agnostic client that serves every outlet.
# Code only — no live keys (the admin downloads those via "Copy .env"). Lives
# under /api/embed/sdk so it shares that path's public SKIP_PREFIXES skip.
_MULTISHOP_DIR = "/app/examples/multishop"
_MULTISHOP_FILES = ["client.php", "client.py", ".env.example", "README.md"]


@router.get("/sdk/gateway-bundle.zip")
def download_gateway_bundle(request: Request):
    """Multi-shop gateway client (php + py + .env.example + README) as one .zip,
    base URL templated for the caller. No keys — caller supplies their own .env."""
    import io
    import os
    import zipfile
    from fastapi.responses import Response

    q = request.query_params
    base = (q.get("base") or _public_base(request) or _SDK_PLACEHOLDERS["base"]).rstrip("/")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in _MULTISHOP_FILES:
            try:
                with open(os.path.join(_MULTISHOP_DIR, name), "r", encoding="utf-8") as f:
                    content = f.read()
            except FileNotFoundError:
                continue  # skip files not deployed
            content = content.replace(_SDK_PLACEHOLDERS["base"], base)
            content = content.replace("__CITYPHARMA_BASE__", base)
            zf.writestr(f"citypharma-shops/{name}", content)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="citypharma-shops.zip"',
            "Cache-Control": "no-store",
        },
    )


# ── One-click WIDGET DEPLOY zip ───────────────────────────────────────────────
# Non-technical handoff: a store gets a .zip that already contains a working
# page (keys baked in) + the paste snippet + a plain-language README. No edits,
# no copy-by-copy. Two routes: per-store (deploy/{embed_id}.zip) + all stores
# (deploy/all.zip). Public — shares the /api/embed/sdk SKIP_PREFIXES family;
# /api/embed/deploy is added to SKIP_PREFIXES in main.py.

def _deploy_embed_rows(embed_id: str | None = None):
    """Fetch embed row(s) for the deploy zip. embed_id=None → all enabled
    embeds for the locked project. Returns list of plain dicts."""
    from sqlalchemy import text
    from dash.embed import _get_engine
    eng = _get_engine()
    cols = ("embed_id, project_slug, name, public_key, bound_scope_id, "
            "primary_color, welcome_msg, position, theme, enabled, status")
    with eng.connect() as conn:
        if embed_id:
            rows = conn.execute(text(
                f"SELECT {cols} FROM public.dash_agent_embeds WHERE embed_id = :e"
            ), {"e": embed_id}).fetchall()
        else:
            rows = conn.execute(text(
                f"SELECT {cols} FROM public.dash_agent_embeds "
                "WHERE COALESCE(enabled, true) = true AND COALESCE(status,'') <> 'disabled' "
                "ORDER BY bound_scope_id NULLS LAST, embed_id"
            )).fetchall()
    return [dict(r._mapping) for r in rows]


def _deploy_files(row: dict, base: str) -> dict[str, str]:
    """Render the 3 drop-in files for one embed, keys baked in. Returns
    {filename: content}. No placeholders left — runs as-is."""
    import html as _html
    eid = row["embed_id"]
    pubkey = row.get("public_key") or ""
    store = row.get("bound_scope_id") or ""
    name = row.get("name") or row.get("project_slug") or "CityPharma"
    title = f"{name}" + (f" — {store}" if store else "")
    # Per-store appearance OVERRIDES (raw, un-defaulted). Only bake a data-*
    # attribute when this store actually overrides — otherwise OMIT it so the
    # widget falls through to /api/embed/config and inherits the live Brand
    # theme. Baking the hard default froze every widget on navy/English and
    # broke the "change Brand → all widgets update live" promise.
    accent_ov   = (row.get("primary_color") or "").strip()
    position_ov = (row.get("position") or "").strip()
    theme_ov    = (row.get("theme") or "").strip()
    welcome_ov  = (row.get("welcome_msg") or "").strip()
    accent = accent_ov or "#1a2b4a"   # page chrome (h1 etc.) still needs a color
    position = position_ov or "bottom-right"
    et = _html.escape(title)
    base = base.rstrip("/")

    _attr_pos = f'        data-position="{position_ov}"\n' if position_ov else ''
    _attr_thm = f'        data-theme="{theme_ov}"\n' if theme_ov else ''
    _attr_acc = f'        data-accent="{accent_ov}"\n' if accent_ov else ''
    _attr_grt = (f'        data-greeting="{_html.escape(welcome_ov, quote=True)}"\n'
                 if welcome_ov else '')
    # the one snippet — same shape as the live sandbox (data-key = public key).
    # Appearance attrs appear only on override; absent → inherit Brand.
    snippet = (
        f'<script src="{base}/api/embed/widget.js"\n'
        f'        data-embed-id="{eid}"\n'
        f'        data-key="{pubkey}"\n'
        f'{_attr_pos}{_attr_thm}{_attr_acc}{_attr_grt}'
        f'        data-title="{et}"\n'
        f'        async></script>'
    )

    index_html = (
        "<!doctype html>\n<html lang=\"en\"><head>\n"
        "<meta charset=\"utf-8\"/>\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>\n"
        f"<title>{et}</title>\n"
        "<style>\n"
        "  body{margin:0;padding:48px 20px;background:#f5f1ea;"
        "font-family:system-ui,-apple-system,'Segoe UI',sans-serif;text-align:center;min-height:100vh;}\n"
        f"  h1{{color:{accent};font-size:26px;margin:32px auto 10px;}}\n"
        "  p{color:#555;font-size:15px;max-width:520px;margin:0 auto 8px;line-height:1.6;}\n"
        "  .pill{display:inline-block;margin-top:18px;padding:4px 12px;background:rgba(0,0,0,.06);"
        "color:#444;border-radius:999px;font-size:11px;letter-spacing:.04em;text-transform:uppercase;}\n"
        "</style>\n</head><body>\n"
        f"  <h1>{et}</h1>\n"
        f"  <p>Tap the chat bubble at the {position.replace('-', ' ')} to ask about stock, "
        "drug info, substitutes and prices.</p>\n"
        + (f"  <span class=\"pill\">store: {_html.escape(store)}</span>\n" if store else "")
        + "  " + snippet + "\n"
        "</body></html>\n"
    )

    readme = (
        f"{title}\n"
        f"{'=' * len(title)}\n\n"
        "Your CityPharma chat assistant — ready to deploy. Pick ONE option.\n\n"
        "OPTION 1 — Host the ready page (easiest)\n"
        "  1. Upload the file 'index.html' to your website / hosting.\n"
        "  2. Open it in a browser. The chat bubble appears bottom corner.\n"
        "  Done. Nothing to edit — your keys are already inside.\n\n"
        "OPTION 2 — Add to an existing website\n"
        "  1. Open 'snippet.html'.\n"
        "  2. Copy everything in it.\n"
        "  3. Paste it just before the </body> tag of your site's pages.\n"
        "  Save + publish. The chat bubble appears on those pages.\n\n"
        "TEST WITHOUT A WEBSITE\n"
        "  Double-click 'index.html' to open it in your browser right now.\n\n"
        "GOOD TO KNOW\n"
        f"  - Store / branch : {store or 'all (global)'}\n"
        f"  - Widget ID      : {eid}\n"
        "  - The assistant only answers about your pharmacy data.\n"
        "  - Need it on a live site? Add your website address to the widget's\n"
        "    allowed origins in the admin Widgets tab (else it shows a 403).\n"
    )
    return {"index.html": index_html, "snippet.html": snippet + "\n", "README.txt": readme}


def _safe_slug(s: str) -> str:
    import re as _re2
    return _re2.sub(r"[^A-Za-z0-9_.-]+", "-", str(s or "")).strip("-") or "store"


@router.get("/deploy/{embed_id}.zip")
def download_deploy_zip(embed_id: str, request: Request):
    """One store's drop-in widget kit (index.html + snippet.html + README),
    keys pre-baked. Non-technical: download → host index.html → live."""
    import io
    import zipfile
    from fastapi.responses import Response
    if embed_id == "all":  # static route would otherwise be shadowed by this param route
        return download_deploy_all_zip(request)
    rows = _deploy_embed_rows(embed_id)
    if not rows:
        raise HTTPException(status_code=404, detail="embed not found")
    row = rows[0]
    base = _public_base(request) or _SDK_PLACEHOLDERS["base"]
    folder = _safe_slug(row.get("bound_scope_id") or row.get("name") or row["embed_id"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, content in _deploy_files(row, base).items():
            zf.writestr(f"{folder}/{fname}", content)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="widget-{folder}.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/deploy/all.zip")
def download_deploy_all_zip(request: Request):
    """Every store's widget kit in one zip — a folder per store + a top-level
    INDEX.html linking them all + HOW-TO-DEPLOY.txt. Admin hands each branch
    its folder."""
    import io
    import zipfile
    import html as _html
    from fastapi.responses import Response
    rows = _deploy_embed_rows(None)
    if not rows:
        raise HTTPException(status_code=404, detail="no embeds to deploy")
    base = _public_base(request) or _SDK_PLACEHOLDERS["base"]
    buf = io.BytesIO()
    index_links = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen: dict[str, int] = {}
        for row in rows:
            folder = _safe_slug(row.get("bound_scope_id") or row.get("name") or row["embed_id"])
            if folder in seen:  # disambiguate collisions
                seen[folder] += 1
                folder = f"{folder}-{seen[folder]}"
            else:
                seen[folder] = 0
            for fname, content in _deploy_files(row, base).items():
                zf.writestr(f"{folder}/{fname}", content)
            label = row.get("bound_scope_id") or row.get("name") or row["embed_id"]
            index_links.append(
                f'    <li><a href="{folder}/index.html">{_html.escape(str(label))}</a></li>'
            )
        index_html = (
            "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"/>\n"
            "<title>CityPharma — store widgets</title>\n"
            "<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;"
            "padding:0 20px;color:#222;}h1{color:#1a2b4a;}li{margin:6px 0;}a{color:#9a4a2f;}</style>\n"
            "</head><body>\n<h1>CityPharma — store widgets</h1>\n"
            f"<p>{len(rows)} store widgets. Open a store's page, or hand each branch its folder.</p>\n"
            "<ul>\n" + "\n".join(index_links) + "\n</ul>\n</body></html>\n"
        )
        zf.writestr("INDEX.html", index_html)
        zf.writestr("HOW-TO-DEPLOY.txt", (
            "CityPharma — store widgets (all branches)\n"
            "=========================================\n\n"
            "This zip has one folder per store. Each folder holds a ready-to-use\n"
            "chat widget with that store's keys already inside.\n\n"
            "TO SEE EVERYTHING : open INDEX.html — it links every store's page.\n\n"
            "TO DEPLOY ONE STORE:\n"
            "  - Send that store its folder.\n"
            "  - They upload 'index.html' to their site (or paste 'snippet.html'\n"
            "    before </body> on an existing site). Read the folder's README.txt.\n\n"
            f"Stores included: {len(rows)}\n"
        ))
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="citypharma-widgets-all.zip"',
            "Cache-Control": "no-store",
        },
    )


# ── Embed logo (uploaded via admin, served publicly to the widget) ────────────
_EMBED_LOGO_DIR = "/app/knowledge/embed_logos"
_LOGO_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "svg": "image/svg+xml", "gif": "image/gif",
}


@router.get("/logo/{name}")
def serve_embed_logo(name: str):
    """Serve an uploaded embed logo with permissive CORS so the widget can load
    it from any origin. Path-traversal-safe (whitelist filename chars)."""
    import os
    import re
    from fastapi.responses import FileResponse
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) or ".." in name:
        raise HTTPException(status_code=404, detail="not found")
    path = os.path.join(_EMBED_LOGO_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="logo not found")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return FileResponse(
        path,
        media_type=_LOGO_MIME.get(ext, "application/octet-stream"),
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _extract_origin(req: Request) -> str | None:
    """Prefer Origin header; fall back to scheme://host of Referer."""
    origin = req.headers.get("Origin") or req.headers.get("origin")
    if origin:
        return origin
    referer = req.headers.get("Referer") or req.headers.get("referer")
    if referer:
        try:
            parts = referer.split("/")
            if len(parts) >= 3:
                return f"{parts[0]}//{parts[2]}"
        except Exception:
            return None
    return None


def _check_sec_fetch(req: Request) -> bool:
    """Block requests that look like same-origin spoofing (Sec-Fetch-Site=none).

    Browser sets Sec-Fetch-Site automatically:
      - 'cross-site'  — legit cross-origin embed
      - 'same-origin' — fine
      - 'same-site'   — fine
      - 'none'        — direct user navigation (curl, address bar) → suspicious for /embed/*

    Returns True if OK, False if request looks crafted.
    """
    sfs = req.headers.get("Sec-Fetch-Site") or req.headers.get("sec-fetch-site")
    if sfs is None:
        # Older browsers / curl don't send it — fall back to origin allowlist (already enforced)
        return True
    return sfs in ("cross-site", "same-origin", "same-site")


def _per_embed_cors_headers(allowed_origins: list[str], request_origin: str | None) -> dict:
    """Echo back the request's origin only if it matches allowlist; else omit.

    Server-level CORS allows '*' for permissiveness, but per-embed we tighten:
    only echo origins explicitly listed in the embed's allowed_origins.
    """
    if request_origin and allowed_origins and request_origin in allowed_origins:
        return {
            "Access-Control-Allow-Origin": request_origin,
            "Access-Control-Allow-Credentials": "false",
            "Vary": "Origin",
        }
    return {}


def _verify_test_token(embed_id: str, token: str, secret_key_hash: str) -> bool:
    """Verify an HMAC-SHA256 sandbox token issued by /test-token."""
    import base64
    import hashlib
    import hmac as _hmac
    import json as _json
    import time as _time
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if payload.get("embed_id") != embed_id:
            return False
        exp = int(payload.get("exp") or 0)
        if exp < int(_time.time()):
            return False
        nonce = str(payload.get("nonce") or "")
        sig = str(payload.get("sig") or "")
        # Must match the minter (app/embed.py gen_test_token): it signs
        # "{embed_id}|{nonce}|{exp}|{claims_canon}" where claims_canon is the
        # sort-keys/compact JSON of the optional claims (empty string when none).
        claims_payload = payload.get("claims")
        claims_canon = (
            _json.dumps(claims_payload, sort_keys=True, separators=(",", ":"))
            if isinstance(claims_payload, dict) and claims_payload else ""
        )
        msg = f"{embed_id}|{nonce}|{exp}|{claims_canon}".encode("utf-8")
        expected = _hmac.new(secret_key_hash.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        return _hmac.compare_digest(sig, expected)
    except Exception:
        return False


@router.get("/try/{embed_id}")
def try_embed_sandbox(embed_id: str, request: Request, token: str | None = None):
    """Live sandbox URL for stakeholder testing. Access gated by embed.access_mode:

    - public: open
    - signed: requires valid ?token= (issued by /test-token)
    - dashboard: requires Authorization Bearer
    - ip_allowlist: client IP must be in test_ip_allowlist
    """
    from fastapi.responses import HTMLResponse
    from sqlalchemy import text
    from dash.embed import _get_engine

    try:
        eng = _get_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT embed_id, project_slug, name, public_key, secret_key_hash, "
                " access_mode, test_ip_allowlist, enabled, status, primary_color, "
                " welcome_msg, position, theme, logo_url "
                "FROM public.dash_agent_embeds WHERE embed_id = :e"
            ), {"e": embed_id}).mappings().first()
    except Exception:
        return HTMLResponse("<h1>500 — sandbox lookup failed</h1>", status_code=500)
    if not row:
        return HTMLResponse("<h1>404 — embed not found</h1>", status_code=404)
    if not row["enabled"] or row.get("status") == "disabled":
        return HTMLResponse("<h1>403 — embed disabled</h1>", status_code=403)

    access_mode = (row.get("access_mode") or "public").lower()
    if access_mode == "signed":
        if not token or not _verify_test_token(embed_id, token, row.get("secret_key_hash") or ""):
            return HTMLResponse("<h1>403 — valid ?token= required</h1>", status_code=403)
    elif access_mode == "dashboard":
        auth_hdr = request.headers.get("Authorization") or ""
        if not auth_hdr.lower().startswith("bearer "):
            return HTMLResponse("<h1>401 — Authorization Bearer required</h1>", status_code=401)
        try:
            from app.auth import validate_token
            user = validate_token(auth_hdr.split(" ", 1)[1])
            if not user:
                return HTMLResponse("<h1>401 — invalid token</h1>", status_code=401)
        except Exception:
            return HTMLResponse("<h1>401 — token validation failed</h1>", status_code=401)
    elif access_mode == "ip_allowlist":
        allow = list(row.get("test_ip_allowlist") or [])
        client_ip = request.client.host if request.client else ""
        if allow and client_ip not in allow:
            return HTMLResponse(f"<h1>403 — IP {client_ip} not in allowlist</h1>", status_code=403)

    project_slug = row["project_slug"]
    proj_name = row["name"] or project_slug
    base_url = str(request.base_url).rstrip("/")
    # Raw per-store overrides — only baked as data-* attrs when set, else the
    # widget inherits the live Brand theme via /api/embed/config.
    primary_ov  = (row.get("primary_color") or "").strip()
    position_ov = (row.get("position") or "").strip()
    theme_ov    = (row.get("theme") or "").strip()
    primary = primary_ov or "#1a2b4a"   # page chrome still needs a color
    position = position_ov or "bottom-right"

    # ── Sandbox claim impersonation — ?claim_store_id=42&claim_role=staff ──
    # Bake into a JSON-encoded JS object passed to widget so live preview can
    # exercise RLS claim flow without HMAC signing.
    import json as _json_mod
    _claim_overrides: dict = {}
    for qk, qv in request.query_params.items():
        if qk.lower().startswith("claim_") and len(qk) > 6:
            _claim_overrides[qk[6:]] = qv
    _claims_json_attr = (
        _json_mod.dumps(_claim_overrides).replace('"', "&quot;")
        if _claim_overrides else ""
    )

    # Appearance attrs only when this store overrides — else inherit Brand.
    _attr_pos = f'          data-position="{position_ov}"\n' if position_ov else ''
    _attr_thm = f'          data-theme="{theme_ov}"\n' if theme_ov else ''
    _attr_acc = f'          data-accent="{primary_ov}"\n' if primary_ov else ''

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"/>
<title>Sandbox — {proj_name}</title>
<style>
  body {{ margin:0; padding:40px; background:#f5f1ea; font-family:system-ui, -apple-system, "Segoe UI", sans-serif; text-align:center; min-height:100vh; }}
  h1 {{ color:#1a2b4a; font-size:28px; margin: 40px auto 12px; }}
  p {{ color:#666; font-size:14px; max-width:520px; margin: 0 auto 8px; line-height:1.55; }}
  .pill {{ display:inline-block; padding:4px 10px; background:rgba(0,0,0,0.06); color:#444; border-radius:999px; font-size:11px; letter-spacing:0.04em; text-transform:uppercase; margin-top:18px; }}
  .hint {{ margin-top: 36px; color:#999; font-size:12px; }}
</style>
</head><body>
  <h1>{proj_name}</h1>
  <p>This is a live test sandbox &mdash; type a question in the chat bubble at the {position.replace('-', ' ')} of this page.</p>
  <span class="pill">project: {project_slug}</span>
  <p class="hint">Powered by the auto-provisioned project embed. Share this URL with stakeholders to test the assistant.</p>

  <script src="{base_url}/api/embed/widget.js"
          data-embed-id="{row['embed_id']}"
          data-key="{row['public_key']}"
{_attr_pos}{_attr_thm}{_attr_acc}          data-title="{proj_name}"
          {('data-claims="' + _claims_json_attr + '"') if _claims_json_attr else ''}
          async></script>
  {('<div style="margin-top:24px;padding:10px 14px;background:#fff;border:1px dashed #c96342;border-radius:6px;display:inline-block;font-size:11px;color:#1a2b4a;">Impersonating claims: <code style="color:#c96342;">' + _json_mod.dumps(_claim_overrides) + '</code></div>') if _claim_overrides else ''}
</body></html>"""

    return HTMLResponse(html, headers={"X-Frame-Options": "SAMEORIGIN"})


def _resolve_starter_questions(row_value) -> list:
    """Resolve the widget's initial starter-question chips:
        per-widget starter_questions (non-empty list)  ?  global embed_default_starters.
    Returns a list of strings (Burmese pharma defaults when nothing is set)."""
    try:
        import json as _json
        v = row_value
        if isinstance(v, str):
            v = _json.loads(v) if v.strip() else []
        if isinstance(v, list) and len(v) > 0:
            return [str(x) for x in v if str(x).strip()]
    except Exception:
        pass
    try:
        from dash.admin.settings import get_setting
        g = get_setting("embed_default_starters", default=[])
        if isinstance(g, list):
            return [str(x) for x in g if str(x).strip()]
    except Exception:
        pass
    return []


@router.get("/config/{embed_id}")
def get_embed_public_config(embed_id: str, request: Request):
    """Return public theme config for the widget. No auth (origin-checked at session)."""
    from sqlalchemy import text
    from dash.embed import _get_engine
    try:
        eng = _get_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT embed_id, name, primary_color, logo_url, welcome_msg, position, theme, "
                " allowed_origins, enabled, status, auth_mode, starter_questions "
                "FROM public.dash_agent_embeds WHERE embed_id = :e"
            ), {"e": embed_id}).first()
    except Exception:
        raise HTTPException(500, "config lookup failed")
    if not row:
        raise HTTPException(404, "embed not found")
    d = dict(row._mapping)
    if d.get("enabled") is False or d.get("status") == "disabled":
        raise HTTPException(403, "embed disabled")
    origin = request.headers.get("origin", "")
    allowed = d.get("allowed_origins") or []
    cors = {}
    if not allowed or "*" in allowed or origin in allowed:
        cors = {
            "Access-Control-Allow-Origin": origin or "*",
            "Vary": "Origin",
        }
    # Single-point brand: resolve each appearance field as
    #   per-widget override (non-empty)  ►  brand default  ►  hard fallback.
    try:
        from app.embed import get_brand_defaults
        brand = get_brand_defaults()
    except Exception:
        brand = {}
    def _resolve(col: str, hard: str) -> str:
        v = d.get(col)
        if v is not None and v != "":
            return v
        bv = brand.get(col)
        return bv if (bv is not None and bv != "") else hard
    # Greeting fallback chain: per-widget welcome_msg ?? brand ?? global
    # embed_default_welcome setting ?? hard Burmese string.
    _welcome_hard = "မင်္ဂလာပါ — ဘာများ ကူညီပေးရမလဲ?"
    try:
        from dash.admin.settings import get_setting as _gs
        _welcome_default = _gs("embed_default_welcome", default=_welcome_hard) or _welcome_hard
    except Exception:
        _welcome_default = _welcome_hard
    payload = {
        "embed_id": d["embed_id"],
        "name": d.get("name"),
        "primary_color": _resolve("primary_color", "#1a2b4a"),
        "logo_url": _resolve("logo_url", "") or None,
        "welcome_msg": _resolve("welcome_msg", _welcome_default),
        "position": _resolve("position", "bottom-right"),
        "theme": _resolve("theme", "auto"),
        "auth_mode": d.get("auth_mode") or "public",
        "starter_questions": _resolve_starter_questions(d.get("starter_questions")),
    }
    from fastapi.responses import JSONResponse
    return JSONResponse(payload, headers=cors)


@router.get("/config/{embed_id}/suggestions")
def get_embed_starter_suggestions(embed_id: str, request: Request):
    """Initial starter-question chips for the widget (shown before the first
    message). Resolves per-embed `starter_questions` ?? global Burmese default.
    Per-ANSWER follow-ups are a separate heuristic (`_consumer_followups`) carried
    in the stream `done` payload — this route only seeds the opening chips."""
    from sqlalchemy import text
    from dash.embed import _get_engine
    from fastapi.responses import JSONResponse
    starters: list = []
    try:
        eng = _get_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT starter_questions, enabled, status "
                "FROM public.dash_agent_embeds WHERE embed_id = :e"
            ), {"e": embed_id}).first()
        if row and not (row[1] is False or row[2] == "disabled"):
            starters = _resolve_starter_questions(row[0])
        else:
            # Unknown/disabled embed → still return the global Burmese defaults
            # so the widget never shows an empty chip row.
            starters = _resolve_starter_questions(None)
    except Exception:
        starters = _resolve_starter_questions(None)
    origin = request.headers.get("origin", "")
    cors = {"Access-Control-Allow-Origin": origin or "*", "Vary": "Origin"}
    return JSONResponse({"questions": starters}, headers=cors)


@router.post("/session/create")
async def create_embed_session(req: Request):
    """Bootstrap a short-lived session token for the widget."""
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid JSON body")

    embed_id = body.get("embed_id")
    public_key = body.get("public_key")
    user_payload = body.get("user")
    signature = body.get("signature")

    if not embed_id or not public_key:
        raise HTTPException(status_code=400, detail="embed_id + public_key required")

    if not _check_sec_fetch(req):
        raise HTTPException(status_code=403, detail="invalid request context")

    origin = _extract_origin(req)
    ip = req.client.host if req.client else None

    # Imported lazily so app start-up isn't blocked if the embed module is
    # mid-deploy.
    from dash.embed.auth import authenticate_session_request, EmbedAuthError
    from dash.embed.session import create_session as _mk_session
    from fastapi.responses import JSONResponse

    server_origin = f"{req.url.scheme}://{req.url.netloc}" if req.url else None
    # Same-origin browsers (preview iframe on dashboard host) may omit the
    # Origin header. Treat referer-from-self as same-origin fallback.
    if not origin and server_origin:
        referer = req.headers.get("referer") or ""
        if referer.startswith(server_origin):
            origin = server_origin
    try:
        ctx = authenticate_session_request(
            embed_id=embed_id,
            public_key=public_key,
            user_payload=user_payload if isinstance(user_payload, dict) else None,
            signature=signature,
            origin=origin,
            ip=ip,
            server_origin=server_origin,
        )
    except EmbedAuthError as e:
        # Machine-readable error w/ code + docs URL. `detail` preserved for
        # back-compat with widgets that parse only that field.
        return JSONResponse(
            {
                "detail": e.detail,
                "code": e.code,
                "docs": f"https://dash.docs/embed/errors#{e.code}",
            },
            status_code=e.status,
        )
    except ValueError as e:
        # Legacy fallback (any uncaught ValueError) — keep prior 403 shape.
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception("embed session/create failed")
        raise HTTPException(status_code=500, detail="internal error")

    token = _mk_session(
        embed_id=ctx["embed_id"],
        external_user=ctx.get("external_user"),
        user_attrs=ctx.get("user_attrs"),
        origin=origin,
        ip=ip,
    )

    # ── RLS claim extraction (Phase 1) ──────────────────────────────────────
    # If embed has RLS enabled, extract claims from the configured source and
    # persist on the session row. Sibling agent owns dash/embed/rls.py; we
    # import lazily + fail-soft so embeds without RLS keep working.
    claims_summary: dict | None = None
    try:
        from dash.embed.rls import load_rls_for_embed, extract_claims  # type: ignore
        # 4-tuple (enabled, claims_def, policies, claim_source) — NOT a dict.
        _rls_enabled, _rls_claims_def, _rls_policies, _rls_source = load_rls_for_embed(ctx["embed_id"])
        if _rls_enabled:
            source = (_rls_source or "token").lower()
            # Build per-source raw input.
            raw: dict = {}
            if source == "token":
                # Token-mode: claims come from the verified signed token
                # payload. authenticate_session_request returns user_attrs from
                # the verified payload — pass them through.
                raw = dict(ctx.get("user_attrs") or {})
                if ctx.get("external_user"):
                    raw.setdefault("user_id", ctx["external_user"])
            elif source == "hmac":
                raw = dict(user_payload) if isinstance(user_payload, dict) else {}
            elif source == "url":
                # ?store_id=42&role=staff on session/create
                raw = {k: v for k, v in req.query_params.items()}
            elif source == "header":
                # x-embed-claim-<key>: <value>
                prefix = "x-embed-claim-"
                for hk, hv in req.headers.items():
                    if hk.lower().startswith(prefix):
                        raw[hk[len(prefix):].lower()] = hv

            # extract_claims(claims_def, source, *, <source>_payload=...). `raw`
            # is the already-resolved per-source bag; pass it to every payload
            # slot (only the one matching `source` is read).
            extracted = extract_claims(
                _rls_claims_def or [], source,
                token_payload=raw, hmac_payload=raw, url_params=raw, headers=raw,
            )
            if extracted:
                claims_summary = extracted
                # Persist on the session row (idempotent column ensured at
                # auth.py import time).
                try:
                    import json as _json
                    from sqlalchemy import text as _sa_text
                    from dash.embed import _get_engine
                    eng = _get_engine()
                    with eng.begin() as conn:
                        conn.execute(
                            _sa_text(
                                "UPDATE public.dash_embed_sessions "
                                "SET claims = CAST(:c AS JSONB) "
                                "WHERE session_token = :t"
                            ),
                            {"c": _json.dumps(extracted), "t": token},
                        )
                except Exception:
                    logger.exception("failed to persist session claims")
    except ImportError:
        # dash/embed/rls.py not yet deployed by sibling agent.
        pass
    except Exception:
        logger.exception("RLS claim extraction failed (fail-open)")

    return {
        "session_token": token,
        "expires_in": 15 * 60,
        "feature_config": ctx.get("feature_config") or {},
        "claims": claims_summary,
    }


# ── Phase 3 — embed chat endpoint + rate limit ─────────────────────────
import threading
import time as _time
from collections import deque

_RATE_BUCKETS: dict[str, deque] = {}
_RATE_LOCK = threading.Lock()


def _rate_limit_check(embed_id: str, limit_per_min: int) -> bool:
    """Sliding-window rate limit per embed_id. Returns True if allowed."""
    now = _time.monotonic()
    cutoff = now - 60.0
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS.setdefault(embed_id, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max(1, int(limit_per_min)):
            return False
        bucket.append(now)
        return True


@router.post("/feedback")
async def embed_feedback(req: Request):
    """Record a 👍/👎 (with optional comment + tags) from an embed widget
    visitor. Anonymous (no user_id); session_token identifies the visitor.
    Flows into the same dash_feedback review + training loop as app chat —
    so embed feedback shows in the admin Like/Dislike dashboard."""
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    token = (body or {}).get("session_token")
    rating = (body.get("rating") or "up").lower()
    if rating not in ("up", "down"):
        rating = "up"
    question = (body.get("question") or "").strip()
    answer = (body.get("answer") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="session_token required")
    if not question and not answer:
        return {"status": "skip"}

    from dash.embed.session import validate_session
    sess = validate_session(token)
    if not sess:
        raise HTTPException(status_code=401, detail="session expired or invalid")
    embed_id = sess["embed_id"]

    from dash.embed import _get_engine
    from sqlalchemy import text as _sa_t
    with _get_engine().connect() as conn:
        row = conn.execute(_sa_t(
            "SELECT project_slug FROM public.dash_agent_embeds WHERE embed_id = :e AND enabled = TRUE"
        ), {"e": embed_id}).first()
    if not row:
        raise HTTPException(status_code=403, detail="embed disabled")
    project_slug = row[0]

    comment = (body.get("comment") or "").strip()
    _tags = body.get("tags") or []
    tags = [str(t).strip() for t in _tags if str(t).strip()][:8] if isinstance(_tags, list) else []

    try:
        from db.session import get_write_engine
        with get_write_engine().begin() as conn:
            conn.execute(_sa_t(
                "INSERT INTO public.dash_feedback "
                "(user_id, project_slug, session_id, question, answer, rating, comment, comment_tags) "
                "VALUES (NULL, :s, :sess, :q, :a, :r, :cm, :tg)"
            ), {"s": project_slug, "sess": f"embed:{embed_id}", "q": question or "(embed answer)",
                "a": answer[:2000], "r": rating, "cm": comment or None, "tg": tags or None})
    except Exception as e:
        logger.warning(f"embed feedback insert failed: {e}")
        raise HTTPException(status_code=500, detail="could not save feedback")
    return {"status": "ok", "saved": rating}


@router.post("/chat")
async def embed_chat(req: Request):
    """Run a chat turn for an embed session.

    Body: {"session_token": "...", "message": "..."}.
    Validates session, applies rate limit, runs team, returns answer.
    user_attrs from session are injected into request context so the
    Analyst's RLS layer can filter rows.
    """
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid JSON body")

    token = body.get("session_token")
    message = (body.get("message") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="session_token required")
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    if len(message) > 50000:
        raise HTTPException(status_code=413, detail="message too long")

    from dash.embed.session import validate_session
    sess = validate_session(token)
    if not sess:
        raise HTTPException(status_code=401, detail="session expired or invalid")

    embed_id = sess["embed_id"]

    # Fetch session claims (populated at session/create when RLS enabled).
    sess_claims: dict | None = None
    try:
        from sqlalchemy import text as _sa_text2
        from dash.embed import _get_engine as _ge
        _eng_c = _ge()
        with _eng_c.connect() as _cc:
            _crow = _cc.execute(_sa_text2(
                "SELECT claims FROM public.dash_embed_sessions WHERE session_token = :t"
            ), {"t": token}).first()
            if _crow and _crow[0]:
                sess_claims = _crow[0] if isinstance(_crow[0], dict) else None
    except Exception:
        pass

    # Look up embed for project_slug + rate_limit + feature_config.
    from dash.embed import _get_engine
    from sqlalchemy import text
    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT project_slug, rate_limit_per_min, feature_config, enabled, "
            "       id, bound_scope_id, bound_intent, bound_role, "
            "       response_style, max_reply_chars "
            "FROM public.dash_agent_embeds WHERE embed_id = :e"
        ), {"e": embed_id}).first()
    if not row or not row[3]:
        raise HTTPException(status_code=403, detail="embed disabled")
    (project_slug, rate_limit, feature_cfg_override, _, embed_pk,
     bound_scope_id, bound_intent, bound_role,
     response_style, max_reply_chars) = row
    bound_intent = bound_intent or "public"  # WHY: legacy rows pre-migration default safely
    response_style = (response_style or "consumer").lower()
    max_reply_chars = int(max_reply_chars or 600)

    if not _rate_limit_check(embed_id, int(rate_limit or 30)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    # ── Redis cache lookup (fail-soft, never blocks chat) ──────────────────
    _cache_site_id = (sess_claims or {}).get("site_id") or bound_scope_id or "_"
    _ck: str | None = None
    try:
        from dash.cache import embed_cache as _ec
        _ck = _ec.cache_key(embed_id, str(_cache_site_id), message)
        cached = _ec.get(_ck)
        if cached:
            try:
                _ec.incr_hit()
            except Exception:
                pass
            return {**cached, "cache_hit": True, "latency_ms": 5}
    except Exception as e:
        logger.warning("embed_cache lookup error: %s", e)
        _ck = None

    # ── Scope guardrail pre-flight (Phase 5) ───────────────────────────────
    try:
        from dash.scope_classifier import classify_question, log_refusal
        decision = classify_question(project_slug, message)
        if decision.refused:
            log_refusal(project_slug, message, decision,
                        embed_id=embed_id,
                        external_user=sess.get("external_user"))
            refusal = decision.refusal_message or "I can't help with that."
            return {
                "content": refusal,
                "session_token": token,
                "external_user": sess.get("external_user"),
                "refused": True,
                "latency_ms": 0,
            }
    except Exception as e:
        logger.warning("embed scope classifier failed (fail-open): %s", e)

    # Inject user context for RLS + visibility policy. Embed-bound values
    # ALWAYS override session-supplied user_attrs so a malicious host cannot
    # widen the scope by signing a different store_id.
    sess_user_attrs = dict(sess.get("user_attrs") or {})
    if bound_scope_id:
        sess_user_attrs["store_id"] = bound_scope_id
    if bound_role:
        sess_user_attrs["role"] = bound_role
    # Synthetic viewer_user_id keeps audit rows attributable while not
    # colliding with real user IDs (always negative).
    synthetic_viewer = -int(embed_pk) if embed_pk is not None else None
    try:
        from dash.tools.skill_refinery import set_request_context
        set_request_context(
            project_slug=project_slug,
            user_id=None,
            agent="embed",
            user_attrs=sess_user_attrs or None,
            external_user=sess.get("external_user"),
            query_intent=bound_intent,
            viewer_user_id=synthetic_viewer,
            viewer_scope_id=bound_scope_id,
            embed_response_style=response_style,
        )
    except Exception:
        pass

    # Wire 3-tier store scope — same enforcement as API Gateway
    _embed_scope_tok = None
    try:
        from app.auth import resolve_api_scope as _resolve_scope
        from dash.api_scope import API_STORE_SCOPE as _EMBED_SCOPE_VAR
        if sess_user_attrs and sess_user_attrs.get("store_id"):
            _embed_user = {
                "store_id": sess_user_attrs.get("store_id", ""),
                "store_ids": sess_user_attrs.get("store_ids", ""),
                "scope_mode": sess_user_attrs.get("scope_mode", "store"),
                # REQUIRED: resolve_api_scope() returns None without this →
                # API_STORE_SCOPE stays None → the SQL tool drops the
                # `WHERE site_code=…` filter → global-total leak. Embeds are
                # store-locked exactly like a store API key.
                "via_api_key": True,
            }
            _embed_scope = _resolve_scope(_embed_user)
            _embed_scope_tok = _EMBED_SCOPE_VAR.set(_embed_scope)
    except Exception:
        pass

    # Audit log: 1 row per chat turn.
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "UPDATE public.dash_embed_sessions SET request_count = COALESCE(request_count,0)+1 "
                "WHERE session_token = :t"
            ), {"t": token})
    except Exception:
        pass

    # ── RLS ContextVar wiring (Phase 3) — set before team.run, reset after.
    _rls_tokens: list = []
    try:
        from dash.embed.rls import (  # type: ignore
            EMBED_CLAIMS, EMBED_RLS_POLICIES, EMBED_RLS_AUDIT_CTX,
            load_rls_for_embed,
        )
        # load_rls_for_embed returns a 4-TUPLE (enabled, claims_def, policies,
        # claim_source) — NOT a dict. Unpack it; treating it as a dict threw
        # AttributeError on every embed chat (caught fail-open, but RLS never
        # applied + log noise).
        _rls_enabled, _rls_claims_def, _rls_policies, _rls_source = load_rls_for_embed(embed_id)
        if _rls_enabled:
            _rls_tokens.append(EMBED_CLAIMS.set(sess_claims or {}))
            _rls_tokens.append(EMBED_RLS_POLICIES.set(_rls_policies or []))
            _rls_tokens.append(EMBED_RLS_AUDIT_CTX.set({
                "embed_id": embed_id,
                "session_token": token,
                "external_user": sess.get("external_user"),
                "project_slug": project_slug,
            }))
    except ImportError:
        # dash/embed/rls.py not yet deployed by sibling agent.
        pass
    except Exception:
        logger.exception("RLS ContextVar wiring failed (fail-open)")

    # Build team + run (re-uses existing project chat path).
    import time as _time
    t0 = _time.monotonic()
    success = True
    err_msg: str | None = None
    content = ""
    _usage = {"tokens_in": 0, "tokens_out": 0, "model": ""}
    try:
        # ── Stock fast-path (no LLM) ──────────────────────────────────────
        # Pure "do we have X in stock?" → answer from stock_check directly,
        # skipping ~12s of model round-trips. Falls through to the agent on any
        # ambiguity. Consumer / non-private embeds hide qty + cost.
        _shortcut_hit = False
        try:
            from dash.tools.stock_shortcut import try_stock_shortcut
            _bound_site = (sess_user_attrs or {}).get("store_id") or ""
            _mask_qty = (response_style == "consumer") or bool(
                bound_intent and bound_intent != "private")
            _sc = try_stock_shortcut(message, site_code=_bound_site, mask_qty=_mask_qty)
            if _sc and _sc.get("answer"):
                content = _sc["answer"]
                _usage = {"tokens_in": 0, "tokens_out": 0, "model": "shortcut/stock"}
                _shortcut_hit = True
                logger.info("stock shortcut hit (%dms, %d match) — no LLM",
                            _sc.get("elapsed_ms", 0), _sc.get("count", 0))
        except Exception:
            logger.debug("stock shortcut skipped", exc_info=True)

        # Team build + run ONLY when the fast-path did not already answer.
        team = None
        if not _shortcut_hit:
            from dash.team import create_project_team
            team = create_project_team(
                project_slug=project_slug,
                agent_name="Embed Agent",
                agent_role="",
                agent_personality="friendly",
                # Per-store team cache key. The store_id is baked into the system
                # prompt at build time; with user_id=None every store collided on
                # one cached team (citypharma_None_<lang>) and reused the FIRST
                # store's baked store_id → cross-store number leak. synthetic_viewer
                # is the per-embed (per-store) negative id → one team per store.
                user_id=synthetic_viewer,
            )

            ctx_note = ""
            if sess.get("external_user"):
                ctx_note += f"\n[EMBED CONTEXT] external_user={sess['external_user']}"
            if sess.get("user_attrs"):
                import json as _json
                ctx_note += f"\n[EMBED CONTEXT] user_attrs={_json.dumps(sess['user_attrs'])}"

            # Rate-limit concurrent agent runs to prevent OpenRouter 429s under
            # load (e.g. 100-shop embed load test). Reuses the chat-tier semaphore
            # from dash.settings so embed traffic shares the same cap as other
            # async chat paths. Offloads the blocking team.run() to a thread so
            # the event loop stays free while we hold the semaphore.
            import asyncio as _asyncio
            try:
                from dash.settings import _get_sem as _llm_get_sem
                _sem = _llm_get_sem("qa_generation")  # chat tier
            except Exception:
                _sem = None
            if _sem is not None:
                async with _sem:
                    response = await _asyncio.to_thread(
                        team.run, message + ctx_note, session_id=f"embed_{token[:16]}"
                    )
            else:
                response = await _asyncio.to_thread(
                    team.run, message + ctx_note, session_id=f"embed_{token[:16]}"
                )
            content = response.content or ""
            _usage = _embed_usage_from_response(response)  # tokens + model for cost

        # WHY: bound_intent is non-private by default for embeds; strip raw
        # numbers from narrative so banding policy is enforced end-to-end.
        if bound_intent and bound_intent != "private":
            try:
                from dash.dashboards.agents.text_guard import sanitize_narrative
                content = sanitize_narrative(content, project_slug, bound_intent)
            except Exception:
                pass

        # Consumer-mode embeds: strip developer-facing tags/code/routing chatter
        # and cap reply length so the widget renders friendly, marketing-grade text.
        if response_style == "consumer":
            _had_banded = "[banded]" in content
            try:
                content = sanitize_consumer_response(content, max_chars=max_reply_chars)
            except Exception:
                logger.exception("sanitize_consumer_response failed")
            if _had_banded and content and "price" not in content.lower():
                content = content.rstrip() + "\n\n_Prices are hidden in this view — items are ranked highest to lowest._"
    except Exception as e:
        logger.exception("embed chat error")
        success = False
        err_msg = str(e)[:500]
    finally:
        # Reset visibility ContextVars so they don't leak across requests.
        try:
            from dash.tools.skill_refinery import set_request_context
            set_request_context(
                query_intent="private",
                viewer_user_id=0,  # WHY: ContextVar set() needs non-None to clear; 0 is safe sentinel
                viewer_scope_id="",
                embed_response_style="",  # clear so non-embed requests don't inherit
            )
        except Exception:
            pass
        # Reset RLS ContextVars in reverse order via stored reset tokens.
        if _rls_tokens:
            try:
                from dash.embed.rls import (  # type: ignore
                    EMBED_CLAIMS, EMBED_RLS_POLICIES, EMBED_RLS_AUDIT_CTX,
                )
                _vars = [EMBED_CLAIMS, EMBED_RLS_POLICIES, EMBED_RLS_AUDIT_CTX]
                for var, tok in zip(_vars, _rls_tokens):
                    try:
                        var.reset(tok)
                    except Exception:
                        pass
            except Exception:
                pass
        # Reset 3-tier store scope ContextVar.
        if _embed_scope_tok is not None:
            try:
                from dash.api_scope import API_STORE_SCOPE as _EMBED_SCOPE_VAR
                _EMBED_SCOPE_VAR.reset(_embed_scope_tok)
            except Exception:
                pass
        latency_ms = int((_time.monotonic() - t0) * 1000)
        # Log per-call audit row (best-effort — never block the response).
        try:
            origin = req.headers.get("Origin") or ""
            ip = req.client.host if req.client else None
            _lb = _embed_log_bodies()
            # Price embed tokens off the real engine model (caller has no alias).
            try:
                from dash.settings import CHAT_MODEL as _ECM, _compute_cost as _ecc
                _emodel = _usage.get("model") or _ECM
                _ecost = _ecc(_emodel, {
                    "prompt_tokens": _usage.get("tokens_in", 0),
                    "completion_tokens": _usage.get("tokens_out", 0),
                })
            except Exception:
                _emodel = _usage.get("model") or "google/gemini-3-flash-preview"
                _ecost = 0.0
            _params = {
                "e": embed_id, "t": token,
                "u": sess.get("external_user"),
                "o": origin, "ip": ip,
                "mc": len(message or ""),
                "rc": len(content or ""),
                "ms": latency_ms,
                "s": success, "err": err_msg,
                "ti": int(_usage.get("tokens_in", 0) or 0),
                "to": int(_usage.get("tokens_out", 0) or 0),
                "cost": float(_ecost or 0.0),
                "emodel": _emodel,
            }
            if _lb:
                _params["mt"] = message
                _params["rt"] = content
            with eng.begin() as conn:
                conn.execute(text(_embed_call_insert_sql(_lb)), _params)
        except Exception:
            pass

    if not success:
        raise HTTPException(status_code=500, detail="chat failed")

    _response = {
        "content": content,
        "session_token": token,
        "external_user": sess.get("external_user"),
        "latency_ms": latency_ms,
        "cache_hit": False,
    }
    if response_style == "consumer":
        try:
            _response["followups"] = _consumer_followups(message, content)
        except Exception:
            _response["followups"] = []

    # ── Redis cache store (fail-soft) ──────────────────────────────────────
    if _ck:
        try:
            from dash.cache import embed_cache as _ec
            # Don't cache transient fields (latency varies, cache_hit must flip).
            _store = {
                "content": content,
                "external_user": sess.get("external_user"),
            }
            _ec.set_indexed(_ck, _store, embed_id, str(_cache_site_id))
            _ec.incr_miss()
        except Exception as e:
            logger.warning("embed_cache store error: %s", e)

    return _response


# ── Phase 6 — SSE streaming embed chat ──────────────────────────────────
# Mirrors POST /chat auth + agent invocation but streams token deltas via
# Server-Sent Events. Only enabled for response_style=analyst — consumer
# mode requires post-hoc sanitization that can't be applied to a stream of
# 5-20 char tokens (would band currency mid-token). Consumer mode returns
# 400 here; callers must use the non-streaming /chat endpoint.
#
# Event types emitted:
#   meta   {session_token, embed_id, started_at}
#   token  {delta: "..."}
#   done   {latency_ms, session_token, cache_hit}
#   error  {detail, code}
# Heartbeat ": heartbeat\n\n" every 15s to keep connections alive.
# Buffer cap 10KB to defend against runaway LLM output.

_STREAM_MAX_BUFFER_BYTES = 10 * 1024  # 10KB
_STREAM_HEARTBEAT_S = 15.0


def _sse_format(event: str, data_obj) -> str:
    """Format a single SSE event frame."""
    import json as _json
    try:
        payload = _json.dumps(data_obj, default=str)
    except Exception:
        payload = _json.dumps({"_serialize_error": True})
    return f"event: {event}\ndata: {payload}\n\n"


# Tool name → friendly compact label + icon for the live activity strip.
_STEP_TOOL_MAP = {
    "run_sql_query": ("Querying inventory", "📊"),
    "stock_check": ("Checking branch stock", "📦"),
    "store_stock_summary": ("Summarising your shelf", "📦"),
    "find_substitutes": ("Finding substitutes", "🔄"),
    "substitutes": ("Finding substitutes", "🔄"),
    "alternatives_for_indication": ("Finding alternatives", "💊"),
    "indication_search": ("Searching by symptom", "🔍"),
    "drug_relationships": ("Looking up drug info", "💊"),
    "drug_profile": ("Looking up drug info", "💊"),
    "interaction_check": ("Checking interactions", "⚠️"),
    "search_all": ("Searching knowledge", "🔍"),
    "discover_tables": ("Mapping the catalog", "🗂️"),
}


def _tool_name_of(data: dict) -> str:
    """Extract the tool name from a tool-call event payload (same logic as
    _step_label's tool branch). Used to whitelist-gate consumer step labels."""
    tool = data.get("tool") or {}
    name = ""
    if isinstance(tool, dict):
        name = tool.get("tool_name") or tool.get("name") or ""
    name = name or data.get("tool_name") or data.get("tool") or ""
    return name if isinstance(name, str) else ""


def _step_label(event_name: str, data: dict) -> tuple[str, str]:
    """Map an Agno tool/reasoning event to a short (label, icon) for the strip."""
    if "Reasoning" in event_name:
        title = (data.get("title") or data.get("reasoning_content") or "Thinking").strip()
        title = " ".join(title.split())[:48]
        return (title or "Thinking", "🧠")
    # tool call
    tool = data.get("tool") or {}
    name = ""
    if isinstance(tool, dict):
        name = tool.get("tool_name") or tool.get("name") or ""
    name = name or data.get("tool_name") or data.get("tool") or ""
    if isinstance(name, str) and name in _STEP_TOOL_MAP:
        lab, ic = _STEP_TOOL_MAP[name]
        return (lab, ic)
    pretty = str(name).replace("_", " ").strip().title() if name else "Working"
    return (pretty[:40] or "Working", "⚙️")


@router.post("/chat/stream")
async def embed_chat_stream(req: Request):
    """SSE-streaming variant of /chat.

    Reuses the same auth/origin/rate-limit/agent-invocation pipeline as
    /chat but streams content via Server-Sent Events. Only available for
    embeds with response_style=analyst (consumer mode requires post-hoc
    sanitization incompatible with token streaming).
    """
    import asyncio as _asyncio
    import time as _time_mod
    from fastapi.responses import StreamingResponse

    # ── Parse + validate body up front so we can fail fast w/ HTTPException ──
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid JSON body")

    token = body.get("session_token")
    message = (body.get("message") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="session_token required")
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    if len(message) > 50000:
        raise HTTPException(status_code=413, detail="message too long")

    from dash.embed.session import validate_session
    sess = validate_session(token)
    if not sess:
        raise HTTPException(status_code=401, detail="session expired or invalid")

    embed_id = sess["embed_id"]

    # Look up embed for project_slug + rate_limit + response_style.
    from dash.embed import _get_engine
    from sqlalchemy import text
    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT project_slug, rate_limit_per_min, feature_config, enabled, "
            "       id, bound_scope_id, bound_intent, bound_role, "
            "       response_style, max_reply_chars "
            "FROM public.dash_agent_embeds WHERE embed_id = :e"
        ), {"e": embed_id}).first()
    if not row or not row[3]:
        raise HTTPException(status_code=403, detail="embed disabled")
    (project_slug, rate_limit, _fc, _, embed_pk,
     bound_scope_id, bound_intent, bound_role,
     response_style, _max_reply_chars) = row
    bound_intent = bound_intent or "public"
    response_style = (response_style or "consumer").lower()

    # Consumer-mode: stream the live ACTIVITY STRIP (step events) so users
    # see what the agent is doing, but DON'T leak per-token deltas — currency
    # banding + qty masking are applied to the FULL answer at the end, then
    # the sanitized text is emitted as a single token + done. Non-consumer
    # (analyst) embeds stream token-by-token as before.
    consumer_mode = (response_style == "consumer")
    max_reply_chars = int(_max_reply_chars or 600)

    # Same per-embed sliding-window rate limit as /chat.
    if not _rate_limit_check(embed_id, int(rate_limit or 30)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    # Scope guardrail pre-flight (same as /chat). Refusals emit as a single
    # token event + done so the widget still renders a friendly message.
    refusal_text: str | None = None
    try:
        from dash.scope_classifier import classify_question, log_refusal
        decision = classify_question(project_slug, message)
        if decision.refused:
            log_refusal(project_slug, message, decision,
                        embed_id=embed_id,
                        external_user=sess.get("external_user"))
            refusal_text = decision.refusal_message or "I can't help with that."
    except Exception as e:
        logger.warning("embed scope classifier failed (fail-open): %s", e)

    # Inject embed user context same as /chat path.
    sess_user_attrs = dict(sess.get("user_attrs") or {})
    if bound_scope_id:
        sess_user_attrs["store_id"] = bound_scope_id
    if bound_role:
        sess_user_attrs["role"] = bound_role
    synthetic_viewer = -int(embed_pk) if embed_pk is not None else None
    try:
        from dash.tools.skill_refinery import set_request_context
        set_request_context(
            project_slug=project_slug,
            user_id=None,
            agent="embed",
            user_attrs=sess_user_attrs or None,
            external_user=sess.get("external_user"),
            query_intent=bound_intent,
            viewer_user_id=synthetic_viewer,
            viewer_scope_id=bound_scope_id,
            embed_response_style=response_style,
        )
    except Exception:
        pass

    # Wire 3-tier store scope — same enforcement as API Gateway
    _embed_scope_tok = None
    try:
        from app.auth import resolve_api_scope as _resolve_scope
        from dash.api_scope import API_STORE_SCOPE as _EMBED_SCOPE_VAR
        if sess_user_attrs and sess_user_attrs.get("store_id"):
            _embed_user = {
                "store_id": sess_user_attrs.get("store_id", ""),
                "store_ids": sess_user_attrs.get("store_ids", ""),
                "scope_mode": sess_user_attrs.get("scope_mode", "store"),
                # REQUIRED: resolve_api_scope() returns None without this →
                # API_STORE_SCOPE stays None → the SQL tool drops the
                # `WHERE site_code=…` filter → global-total leak. Embeds are
                # store-locked exactly like a store API key.
                "via_api_key": True,
            }
            _embed_scope = _resolve_scope(_embed_user)
            _embed_scope_tok = _EMBED_SCOPE_VAR.set(_embed_scope)
    except Exception:
        pass

    # Bump request counter (best-effort).
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "UPDATE public.dash_embed_sessions "
                "SET request_count = COALESCE(request_count,0)+1 "
                "WHERE session_token = :t"
            ), {"t": token})
    except Exception:
        pass

    # RLS ContextVar wiring (mirror /chat).
    _rls_tokens: list = []
    try:
        from dash.embed.rls import (  # type: ignore
            EMBED_CLAIMS, EMBED_RLS_POLICIES, EMBED_RLS_AUDIT_CTX,
            load_rls_for_embed,
        )
        sess_claims: dict | None = None
        try:
            from sqlalchemy import text as _sa_text2
            with eng.connect() as _cc:
                _crow = _cc.execute(_sa_text2(
                    "SELECT claims FROM public.dash_embed_sessions WHERE session_token = :t"
                ), {"t": token}).first()
                if _crow and _crow[0]:
                    sess_claims = _crow[0] if isinstance(_crow[0], dict) else None
        except Exception:
            pass
        # load_rls_for_embed returns a 4-TUPLE (enabled, claims_def, policies,
        # claim_source) — NOT a dict. Unpack it; treating it as a dict threw
        # AttributeError on every embed chat (caught fail-open, but RLS never
        # applied + log noise).
        _rls_enabled, _rls_claims_def, _rls_policies, _rls_source = load_rls_for_embed(embed_id)
        if _rls_enabled:
            _rls_tokens.append(EMBED_CLAIMS.set(sess_claims or {}))
            _rls_tokens.append(EMBED_RLS_POLICIES.set(_rls_policies or []))
            _rls_tokens.append(EMBED_RLS_AUDIT_CTX.set({
                "embed_id": embed_id,
                "session_token": token,
                "external_user": sess.get("external_user"),
                "project_slug": project_slug,
            }))
    except ImportError:
        pass
    except Exception:
        logger.exception("RLS ContextVar wiring failed (fail-open)")

    # ── SSE producer ─────────────────────────────────────────────────────
    async def _produce():
        from datetime import datetime, timezone
        t0 = _time_mod.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        full_buffer: list[str] = []
        _stream_usage = {"tokens_in": 0, "tokens_out": 0, "model": ""}  # for cost
        buffer_bytes = 0
        capped = False
        last_step_label = ""
        _reasoning_shown = False
        _shown_steps = 0
        _MAX_CONSUMER_STEPS = 6
        # Consumer incremental-streamer cursor: number of SANITIZED characters
        # already emitted as 'token' deltas. We never re-emit before this point.
        emitted_len = 0

        # meta event first.
        yield _sse_format("meta", {
            "session_token": token,
            "embed_id": embed_id,
            "started_at": started_at,
        })

        # Short-circuit: refusal path emits one token + done.
        if refusal_text is not None:
            yield _sse_format("token", {"delta": refusal_text})
            yield _sse_format("done", {
                "latency_ms": int((_time_mod.monotonic() - t0) * 1000),
                "session_token": token,
                "cache_hit": False,
                "refused": True,
            })
            return

        # ── Stock fast-path (no LLM) — pure "do we have X?" answers in code,
        # skipping ~12s of model round-trips. Falls through on any ambiguity.
        try:
            from dash.tools.stock_shortcut import try_stock_shortcut
            _sc_site = (sess_user_attrs or {}).get("store_id") or ""
            _sc_mask = consumer_mode or bool(bound_intent and bound_intent != "private")
            _sc = try_stock_shortcut(message, site_code=_sc_site, mask_qty=_sc_mask)
        except Exception:
            _sc = None
        if _sc and _sc.get("answer"):
            yield _sse_format("step", {"label": "Checking stock", "icon": "🔍"})
            _sc_answer = _sc["answer"]
            full_buffer.append(_sc_answer)
            yield _sse_format("token", {"delta": _sc_answer})
            yield _sse_format("done", {
                "latency_ms": int((_time_mod.monotonic() - t0) * 1000),
                "session_token": token,
                "cache_hit": False,
                "shortcut": "stock",
            })
            logger.info("stock shortcut hit (stream, %dms, %d match) — no LLM",
                        _sc.get("elapsed_ms", 0), _sc.get("count", 0))
            # Audit row (best-effort) — keep shortcut hits visible in usage stats.
            try:
                _lb = _embed_log_bodies()
                _sc_params = {
                    "e": embed_id, "t": token, "u": sess.get("external_user"),
                    "o": req.headers.get("Origin") or "",
                    "ip": req.client.host if req.client else None,
                    "mc": len(message or ""), "rc": len(_sc_answer or ""),
                    "ms": int((_time_mod.monotonic() - t0) * 1000),
                    "s": True, "err": None, "ti": 0, "to": 0,
                    "cost": 0.0, "emodel": "shortcut/stock",
                }
                if _lb:
                    _sc_params["mt"] = message
                    _sc_params["rt"] = _sc_answer
                with eng.begin() as conn:
                    conn.execute(text(_embed_call_insert_sql(_lb)), _sc_params)
            except Exception:
                pass
            return

        try:
            from dash.team import create_project_team
            team = create_project_team(
                project_slug=project_slug,
                agent_name="Embed Agent",
                agent_role="",
                agent_personality="friendly",
                # Per-store team cache key (see chat path) — stops cross-store
                # baked-prompt reuse under the shared citypharma_None_<lang> key.
                user_id=synthetic_viewer,
            )

            ctx_note = ""
            if sess.get("external_user"):
                ctx_note += f"\n[EMBED CONTEXT] external_user={sess['external_user']}"
            if sess.get("user_attrs"):
                import json as _json
                ctx_note += f"\n[EMBED CONTEXT] user_attrs={_json.dumps(sess['user_attrs'])}"

            # Stream from Agno team. Pump a sync iterator into an asyncio
            # queue so the producer coroutine can interleave heartbeats
            # without blocking on team.run().
            queue: _asyncio.Queue = _asyncio.Queue(maxsize=256)
            _SENTINEL = object()
            error_holder: dict = {}
            loop = _asyncio.get_running_loop()

            def _pump_sync():
                try:
                    it = team.run(
                        message + ctx_note,
                        session_id=f"embed_{token[:16]}",
                        stream=True,
                        stream_events=True,
                    )
                    for event in it:
                        _asyncio.run_coroutine_threadsafe(
                            queue.put(event), loop
                        ).result(timeout=30)
                except Exception as exc:
                    error_holder["err"] = str(exc)[:500]
                finally:
                    try:
                        _asyncio.run_coroutine_threadsafe(
                            queue.put(_SENTINEL), loop
                        ).result(timeout=5)
                    except Exception:
                        pass

            pump_task = _asyncio.create_task(_asyncio.to_thread(_pump_sync))
            last_heartbeat = _time_mod.monotonic()

            while True:
                try:
                    event = await _asyncio.wait_for(queue.get(), timeout=_STREAM_HEARTBEAT_S)
                except _asyncio.TimeoutError:
                    # Heartbeat keeps proxies (nginx, Caddy) from closing.
                    yield ": heartbeat\n\n"
                    last_heartbeat = _time_mod.monotonic()
                    continue

                if event is _SENTINEL:
                    break

                # Extract delta. Agno yields TeamRunContent / RunContent
                # events w/ a .content delta. Skip tool calls + reasoning
                # steps — embed UX is final answer only (admin trace owns
                # the rest).
                try:
                    if hasattr(event, "to_dict"):
                        data = event.to_dict()
                    elif hasattr(event, "model_dump"):
                        data = event.model_dump()
                    elif hasattr(event, "__dict__"):
                        data = dict(event.__dict__)
                    else:
                        data = {"content": str(event)}
                except Exception:
                    data = {}

                event_name = data.get("event") or type(event).__name__

                # Accumulate token usage from completion events' metrics (same
                # shape projects.py reads) so embed cost is priced, not $0.
                try:
                    _mt = data.get("metrics") if isinstance(data.get("metrics"), dict) else None
                    if _mt:
                        _it = _mt.get("input_tokens"); _ot = _mt.get("output_tokens")
                        _it = sum(_it) if isinstance(_it, (list, tuple)) else (_it or 0)
                        _ot = sum(_ot) if isinstance(_ot, (list, tuple)) else (_ot or 0)
                        _stream_usage["tokens_in"] += int(_it)
                        _stream_usage["tokens_out"] += int(_ot)
                        if _mt.get("model"):
                            _stream_usage["model"] = str(_mt["model"])
                except Exception:
                    pass

                # Forward a COMPACT activity step for tool / reasoning events
                # so the widget can show "what the agent is doing". Final
                # answer still streams via token events below.
                _is_reasoning = "Reasoning" in event_name
                if event_name in (
                    "ToolCallStarted", "TeamToolCallStarted",
                    "ReasoningStep", "TeamReasoningStep", "ReasoningStarted",
                ):
                    # Consumer mode: NEVER stream the model's raw reasoning-step
                    # titles — they are unsanitized model text and leak garbage
                    # (e.g. a hallucinated "music" title) into the end-user strip.
                    # Collapse all reasoning to ONE generic "Thinking…" and only
                    # show friendly whitelisted tool labels.
                    if consumer_mode and _is_reasoning:
                        if not _reasoning_shown:
                            _reasoning_shown = True
                            yield _sse_format("step", {"label": "Thinking", "icon": "🧠"})
                        continue
                    # Consumer mode: WHITELIST-ONLY tool steps. An un-mapped tool
                    # (team delegation/transfer, internal orchestration — e.g.
                    # "delegate_task_to_member") must NOT leak its raw name into
                    # the customer strip; collapse it to one generic "Thinking".
                    if consumer_mode and not _is_reasoning:
                        _tname = _tool_name_of(data)
                        if _tname not in _STEP_TOOL_MAP:
                            if not _reasoning_shown:
                                _reasoning_shown = True
                                yield _sse_format("step", {"label": "Thinking", "icon": "🧠"})
                            continue
                    label, icon = _step_label(event_name, data)
                    # Consumer mode: cap visible distinct steps so a 27-step run
                    # doesn't flood the bubble.
                    if consumer_mode and _shown_steps >= _MAX_CONSUMER_STEPS:
                        continue
                    if label and label != last_step_label:
                        last_step_label = label
                        _shown_steps += 1
                        yield _sse_format("step", {"label": label, "icon": icon})
                    continue

                delta = ""
                if event_name in ("TeamRunContent", "RunContent"):
                    delta = data.get("content") or ""
                elif not event_name and hasattr(event, "content"):
                    delta = getattr(event, "content", "") or ""

                if not delta:
                    continue

                # Buffer cap defense — once exceeded, stop forwarding tokens
                # but keep draining the pump so team.run() loop completes.
                if buffer_bytes >= _STREAM_MAX_BUFFER_BYTES:
                    capped = True
                    continue

                delta_bytes = len(delta.encode("utf-8"))
                if buffer_bytes + delta_bytes > _STREAM_MAX_BUFFER_BYTES:
                    remaining = _STREAM_MAX_BUFFER_BYTES - buffer_bytes
                    delta = delta.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
                    capped = True

                full_buffer.append(delta)
                buffer_bytes += len(delta.encode("utf-8"))

                if not consumer_mode:
                    # Analyst embeds stream raw token deltas as before.
                    yield _sse_format("token", {"delta": delta})
                else:
                    # ── Consumer incremental SAFE streamer ──────────────────
                    # Token-by-token like ChatGPT while masking stays 100% safe.
                    # HOLD-WINDOW INVARIANT: we sanitize+commit only the prefix
                    # of the running buffer that `_consumer_hold_len` proves can
                    # contain no half-open sensitive token ([TAG:, ``` fence,
                    # <code>, partial md-table-sep). The trailing hold window is
                    # NEVER emitted — it is re-examined on the next delta once
                    # more characters arrive (the token either closes → becomes
                    # committable, or stays open → keeps being held). The FULL
                    # `sanitize_consumer_response` at stream end is the source of
                    # truth and flushes any safe remainder. Conservative: when in
                    # doubt we hold rather than emit (masking > smoothness).
                    raw_running = "".join(full_buffer)
                    hold = _consumer_hold_len(raw_running)
                    committable = raw_running[:len(raw_running) - hold] if hold else raw_running
                    sanitized = _sanitize_fragment(committable)
                    if len(sanitized) > emitted_len:
                        new_text = sanitized[emitted_len:]
                        emitted_len = len(sanitized)
                        if new_text:
                            yield _sse_format("token", {"delta": new_text})

                now = _time_mod.monotonic()
                if now - last_heartbeat >= _STREAM_HEARTBEAT_S:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now

            try:
                await _asyncio.wait_for(pump_task, timeout=5.0)
            except Exception:
                pass

            if error_holder.get("err"):
                yield _sse_format("error", {
                    "detail": error_holder["err"],
                    "code": "agent_error",
                })
                return

            # Consumer-mode end of stream: run the FULL sanitizer (with
            # max_chars truncation, banded-price note, blank-line collapse) on
            # the complete buffer — this is the source of truth. Emit only the
            # remainder BEYOND what the incremental streamer already committed.
            final_for_followups = ""
            if consumer_mode:
                final = "".join(full_buffer)
                try:
                    if bound_intent and bound_intent != "private":
                        from dash.dashboards.agents.text_guard import sanitize_narrative
                        final = sanitize_narrative(final, project_slug, bound_intent)
                except Exception:
                    pass
                # Note once if prices were masked (so the ranked list reads as
                # intentional, not broken) — detected before the tail-strip.
                _had_banded = "[banded]" in final
                try:
                    final = sanitize_consumer_response(final, max_chars=max_reply_chars)
                except Exception:
                    logger.exception("sanitize_consumer_response (stream) failed")
                if _had_banded and final and "price" not in final.lower():
                    final = final.rstrip() + "\n\n_Prices are hidden in this view — items are ranked highest to lowest._"
                final_for_followups = final
                # If the fully-sanitized final is LONGER than what we streamed
                # (the held tail + the appended price-note), flush the new chars.
                # If truncation made it SHORTER than emitted (rare), do nothing —
                # already-emitted text is acceptable; we never retract.
                if final and len(final) > emitted_len:
                    yield _sse_format("token", {"delta": final[emitted_len:]})

            latency_ms = int((_time_mod.monotonic() - t0) * 1000)
            done_payload = {
                "latency_ms": latency_ms,
                "session_token": token,
                "cache_hit": False,
            }
            if capped:
                done_payload["truncated"] = True
            # Per-answer follow-up suggestions (zero-latency heuristic).
            try:
                done_payload["followups"] = _consumer_followups(message, final_for_followups)
            except Exception:
                done_payload["followups"] = []
            yield _sse_format("done", done_payload)

        except Exception as exc:
            logger.exception("embed chat stream error")
            yield _sse_format("error", {
                "detail": str(exc)[:500],
                "code": "stream_error",
            })
        finally:
            try:
                from dash.tools.skill_refinery import set_request_context
                set_request_context(
                    query_intent="private",
                    viewer_user_id=0,
                    viewer_scope_id="",
                    embed_response_style="",
                )
            except Exception:
                pass
            if _rls_tokens:
                try:
                    from dash.embed.rls import (  # type: ignore
                        EMBED_CLAIMS, EMBED_RLS_POLICIES, EMBED_RLS_AUDIT_CTX,
                    )
                    _vars = [EMBED_CLAIMS, EMBED_RLS_POLICIES, EMBED_RLS_AUDIT_CTX]
                    for var, tok in zip(_vars, _rls_tokens):
                        try:
                            var.reset(tok)
                        except Exception:
                            pass
                except Exception:
                    pass
            # Reset 3-tier store scope ContextVar.
            if _embed_scope_tok is not None:
                try:
                    from dash.api_scope import API_STORE_SCOPE as _EMBED_SCOPE_VAR
                    _EMBED_SCOPE_VAR.reset(_embed_scope_tok)
                except Exception:
                    pass

            # Audit log (best-effort) — one row per stream call.
            try:
                content_assembled = "".join(full_buffer)
                latency_ms = int((_time_mod.monotonic() - t0) * 1000)
                origin = req.headers.get("Origin") or ""
                ip = req.client.host if req.client else None
                _lb = _embed_log_bodies()
                try:
                    from dash.settings import CHAT_MODEL as _ECM, _compute_cost as _ecc
                    _emodel = _stream_usage.get("model") or _ECM
                    _ecost = _ecc(_emodel, {
                        "prompt_tokens": _stream_usage.get("tokens_in", 0),
                        "completion_tokens": _stream_usage.get("tokens_out", 0),
                    })
                except Exception:
                    _emodel = _stream_usage.get("model") or "google/gemini-3-flash-preview"
                    _ecost = 0.0
                _params = {
                    "e": embed_id, "t": token,
                    "u": sess.get("external_user"),
                    "o": origin, "ip": ip,
                    "mc": len(message or ""),
                    "rc": len(content_assembled or ""),
                    "ms": latency_ms,
                    "s": True, "err": None,
                    "ti": int(_stream_usage.get("tokens_in", 0) or 0),
                    "to": int(_stream_usage.get("tokens_out", 0) or 0),
                    "cost": float(_ecost or 0.0),
                    "emodel": _emodel,
                }
                if _lb:
                    _params["mt"] = message
                    _params["rt"] = content_assembled
                with eng.begin() as conn:
                    conn.execute(text(_embed_call_insert_sql(_lb)), _params)
            except Exception:
                pass

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # nginx hint: don't buffer SSE
    }
    # Per-embed CORS echo so the browser accepts SSE from cross-origin pages.
    try:
        with eng.connect() as conn:
            row2 = conn.execute(text(
                "SELECT allowed_origins FROM public.dash_agent_embeds WHERE embed_id = :e"
            ), {"e": embed_id}).first()
            allowed = list(row2[0]) if row2 and row2[0] else []
            origin_hdr = _extract_origin(req)
            headers.update(_per_embed_cors_headers(allowed, origin_hdr))
    except Exception:
        pass

    return StreamingResponse(
        _produce(),
        media_type="text/event-stream",
        headers=headers,
    )

