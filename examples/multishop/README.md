# CityPharma — deploy all shops, one client

One key-agnostic client. No per-shop code. The same file serves every outlet —
add a shop later = add one `.env` line, zero code change.

## Files

| File | What |
|---|---|
| `client.php` | PHP CLI/library — streams answer + live agent thinking |
| `client.py` | Python (stdlib + `requests`) — same |
| `.env.example` | config template — replace with the admin **Copy .env** download |

## 3-step deploy

1. **Get the keys.** Admin → Gateway → **Outlet Keys** → **Copy .env**. That
   file has `CITYPHARMA_BASE` + one `CITYPHARMA_KEY_<outlet>` per branch.
2. **Drop it in.** Save it as `.env` next to the client.
3. **Run.**
   ```bash
   php client.php "is paracetamol in stock at my branch?"        # every shop
   php client.php "..." 20003-CCJ8                                # one shop
   # or
   pip install requests
   python client.py "is paracetamol in stock at my branch?"
   ```

Answer streams to **stdout**; the live agent-thinking trace
(`⟳ 📦 Checking branch stock` …) streams to **stderr**.

## How the features work (already wired — don't change)

| Feature | How |
|---|---|
| **Streaming** | request body `"stream": true` → SSE `chat.completion.chunk` frames |
| **Live thinking** | header `X-Agent-Steps: 1` → extra `delta.x_agent_step:{label,icon}` frames |
| **Proper format** | `Medicine·Salt·Stock·Price` table + Tip — built **server-side**, automatic |
| **3-tier masking** | enforced by the key — own shop = full qty/price, others masked, ref global |

## ⚠️ Do NOT swap to an official SDK for the thinking trace

`x_agent_step` is a **non-standard** delta key. Official OpenAI SDKs
(openai-python, openai-php) silently drop unknown delta fields → you get the
answer but **lose the live thinking strip**. These clients parse raw SSE, so
they keep it. Use the SDK only if you don't need the trace.

## Embedding in your app

`ask_shop(...)` (both files) is a reusable function:
- `client.php` → `ask_shop($base, $key, $question, $onToken, $onThink)`
- `client.py` → `ask_shop(base, key, question, on_token, on_think)`

Wire `on_token` to your response stream and `on_think` to a status line / SSE
to your own frontend — the live strip works end-to-end in your UI too.
