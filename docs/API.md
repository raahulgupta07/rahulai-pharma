# CityPharma — OpenAI-Compatible API

External apps (PHP, Node, Python, anything with an OpenAI SDK) talk to the
CityPharma analyst by pointing a standard OpenAI client at the gateway.

```
base_url = https://<host>/api/v1
api_key  = dash-key-XXXXXXXX        (sent as Authorization: Bearer dash-key-...)
model    = citypharma-analyst
```

No SDK changes — just swap the base URL + key. The gateway reuses the real
agent pipeline and returns OpenAI-shaped JSON.

---

## Authentication

Every request needs a service-account API key in the header:

```
Authorization: Bearer dash-key-XXXXXXXX
```

Keys are minted by a **super-admin** (see "Minting keys" below). Each key is
**bound to one store** and carries a scope mode that decides what data the
caller can see.

### Access model — 3 tiers (the security boundary)

Each key is bound to one store (`store_id`) with a `scope_mode`:

| scope_mode | Behaviour |
|---|---|
| `store` | Tiered visibility (the default for storefront keys). |
| `global` | No masking — full data + raw SQL/aggregates. Internal/admin use only. |

For a `store`-scoped key:

| Tier | Data | Visibility |
|---|---|---|
| **1 — own store** (row.site_code == key.store_id) | drug + **stock qty + cost** | full |
| **2 — other stores** (row.site_code != key.store_id) | drug **availability only** | NO qty, NO cost/price |
| **3 — reference/global** (no site_code: catalog, substitutes, indications) | full | unrestricted |

Enforcement is the **toolset**, not the prompt: store-scoped keys get the
curated pharma tools only; raw `run_sql_query` and aggregate tools are removed
at build time, so prompt injection can't pull cross-store quantities.

---

## Endpoints

### `GET /api/v1/models`

Lists the single virtual model.

```bash
curl https://<host>/api/v1/models \
  -H "Authorization: Bearer dash-key-XXXX"
```

```json
{
  "object": "list",
  "data": [
    { "id": "citypharma-analyst", "object": "model", "created": 0, "owned_by": "citypharma" }
  ]
}
```

### `POST /api/v1/chat/completions`

Standard OpenAI chat completion. Blocking (`stream:false`) or streaming
(`stream:true`).

**Request**

```json
{
  "model": "citypharma-analyst",
  "messages": [
    { "role": "user", "content": "is paracetamol in stock at my branch?" }
  ],
  "stream": false
}
```

Notes:
- Only the **last user message** is used as the question. Prior turns are used
  to derive a stable `session_id` so multi-turn threads keep context server-side.
- Optionally send `"session_id": "abc"` (or OpenAI's `"user": "abc"`) to pin a
  conversation thread.
- Max message length 50,000 chars.

**Blocking response** (`stream:false`)

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1780000000,
  "model": "citypharma-analyst",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "Site 20060-CCBHSC holds 820 units of Paracetamol..." },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 9, "completion_tokens": 41, "total_tokens": 50 },
  "x_session_id": "api-..."
}
```

**Streaming response** (`stream:true`) — `text/event-stream`:

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Site 20060..."},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

---

## Minting + revoking keys (super-admin)

These run from a **logged-in human super-admin session** (a `dash-key-*`
bearer is rejected — keys can't mint other keys).

### Mint

```bash
curl -X POST https://<host>/api/auth/api-key \
  -H "Authorization: Bearer <SUPER_ADMIN_SESSION_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"service_account_name":"php-store-20060","store_id":"20060-CCBHSC","scope_mode":"store"}'
```

```json
{
  "status": "ok",
  "service_account_name": "php-store-20060",
  "service_account_id": 2,
  "store_id": "20060-CCBHSC",
  "scope_mode": "store",
  "api_key": "dash-key-XXXXXXXX"
}
```

The `api_key` is shown **once** — store it securely. Re-minting the same
`service_account_name` rotates the key + rebinds store/scope. Mint also grants
the service account read access to the locked CityPharma project automatically.

### Revoke

```bash
curl -X POST https://<host>/api/auth/api-key/revoke \
  -H "Authorization: Bearer <SUPER_ADMIN_SESSION_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"service_account_name":"php-store-20060"}'
```

(or `{"key":"dash-key-XXXX"}`). Disables the key; keeps the row + store
binding for audit/re-mint.

---

## Errors

| Status | Meaning |
|---|---|
| 401 | Missing/invalid API key |
| 400 | Bad body — non-JSON, empty `messages`, no user message |
| 413 | Message too long (>50,000 chars) |
| 403 | (mint/revoke) not a super-admin session, or api-key bearer |

---

## PHP example

See [`examples/php-openai-client.php`](../examples/php-openai-client.php).
Uses `openai-php/client`:

```bash
composer require openai-php/client
```

```php
$client = OpenAI::factory()
    ->withBaseUri('https://<host>/api/v1')
    ->withApiKey('dash-key-XXXX')
    ->make();

$result = $client->chat()->create([
    'model' => 'citypharma-analyst',
    'messages' => [
        ['role' => 'user', 'content' => 'is paracetamol in stock at my branch?'],
    ],
]);

echo $result->choices[0]->message->content;
```
