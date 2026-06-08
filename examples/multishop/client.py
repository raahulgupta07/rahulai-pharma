#!/usr/bin/env python3
"""CityPharma multi-shop client — ONE file, every outlet.

No per-shop code. Drop your `.env` (the admin "Copy .env" download — all 53
outlet keys) next to this file and run. Each shop is one CITYPHARMA_KEY_* entry;
this client streams the answer AND the live agent-thinking trace.

    python client.py "is paracetamol in stock at my branch?"      # ask every shop
    python client.py "..." 20003-CCJ8                              # ask one shop

Warm pharmacist formatting (Medicine·Salt·Stock·Price + Tip) is produced
server-side, so it lands automatically. The live thinking trace needs the raw
SSE parse below + header "X-Agent-Steps: 1" — official OpenAI SDKs drop the
non-standard x_agent_step frames, so do NOT swap this for an SDK.

Requires: pip install requests
"""
import json
import sys

import requests

MODEL = "citypharma-analyst"


def load_shops(env_path: str):
    """Return (base, {outlet: key}) from a .env file."""
    import os
    if not os.path.isfile(env_path):
        sys.exit(f"missing {env_path} — download it from the admin 'Copy .env' button")
    base, shops = None, {}
    for line in open(env_path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = (s.strip() for s in line.split("=", 1))
        if k == "CITYPHARMA_BASE":
            base = v.rstrip("/")
        elif k.startswith("CITYPHARMA_KEY_") and v:
            shops[k[len("CITYPHARMA_KEY_"):].replace("_", "-")] = v
    if not base:
        sys.exit("no CITYPHARMA_BASE in .env")
    if not shops:
        sys.exit("no CITYPHARMA_KEY_* in .env")
    return base, shops


def ask_shop(base, key, question, on_token, on_think) -> str:
    """Ask one shop. Calls on_token(str) per answer chunk, on_think(label, icon)
    per live agent step. Returns the full answer."""
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}",
                 "X-Agent-Steps": "1"},          # opt-in: live agent thinking
        json={"model": MODEL, "stream": True,
              "messages": [{"role": "user", "content": question}]},
        stream=True,
    )
    answer = ""
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        delta = json.loads(data)["choices"][0].get("delta", {})
        step = delta.get("x_agent_step")           # live thinking trace
        if step:
            on_think(step.get("label", ""), step.get("icon", ""))
        chunk = delta.get("content")               # answer tokens
        if chunk:
            answer += chunk
            on_token(chunk)
    return answer


if __name__ == "__main__":
    question = sys.argv[1] if len(sys.argv) > 1 else "is paracetamol in stock at my branch?"
    only = sys.argv[2] if len(sys.argv) > 2 else None
    base, shops = load_shops("__DIR__/.env".replace("__DIR__", __file__.rsplit("/", 1)[0] or "."))
    if only:
        shops = {k: v for k, v in shops.items() if k == only}

    for outlet, key in shops.items():
        print(f"\n=== {outlet} ===", file=sys.stderr)
        ask_shop(
            base, key, question,
            on_token=lambda t: (sys.stdout.write(t), sys.stdout.flush()),
            on_think=lambda label, icon: print(f"  ⟳ {icon} {label}", file=sys.stderr),
        )
        print()
