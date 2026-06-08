#!/usr/bin/env python3
"""
rest_client.py — drop-in Python SDK for the CityAgent Pharma embed API.

Stdlib only (urllib + hmac). No pip install. Python 3.8+.

    from rest_client import CityAgent
    ca = CityAgent(BASE, EMBED_ID, PUBLIC_KEY, SECRET_KEY, origin="https://yoursite.com")
    print(ca.ask("is paracetamol in stock at my branch?",
                 user={"id": "alice", "store_id": "20063-CCBRBKMY", "role": "staff"}))

Pass user=None for anonymous public mode (tier-3 global/catalog scope).
SECRET_KEY stays server-side; never ship to a browser.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import time
import urllib.request
import urllib.error
from typing import Callable, Optional


class CityAgentError(RuntimeError):
    pass


class CityAgent:
    def __init__(self, base_url: str, embed_id: str, public_key: str,
                 secret_key: Optional[str] = None, origin: Optional[str] = None,
                 timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.embed_id = embed_id
        self.public_key = public_key
        self.secret_key = secret_key
        self.origin = origin
        self.timeout = timeout
        self._session: Optional[str] = None
        self._exp: float = 0.0

    @staticmethod
    def canonical(user: dict) -> str:
        # sorted keys, no spaces — must byte-match the server
        return json.dumps(user, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def sign(self, user: dict) -> str:
        if not self.secret_key:
            raise CityAgentError("secret_key required for user-scoped (hmac) mode")
        return hmac.new(self.secret_key.encode(), self.canonical(user).encode(),
                        hashlib.sha256).hexdigest()

    def session(self, user: Optional[dict] = None) -> str:
        if self._session and time.time() < self._exp - 30:
            return self._session
        body = {"embed_id": self.embed_id, "public_key": self.public_key}
        if user is not None:
            body["user"] = user
            body["signature"] = self.sign(user)
        res = self._post("/api/embed/session/create", body)
        tok = res.get("session_token")
        if not tok:
            raise CityAgentError(f"no session_token in response: {res}")
        self._session = tok
        self._exp = time.time() + int(res.get("expires_in", 900))
        return tok

    def chat(self, message: str, user: Optional[dict] = None) -> str:
        tok = self.session(user)
        res = self._post("/api/embed/chat", {"session_token": tok, "message": message})
        return res.get("content", "")

    ask = chat  # one-liner alias

    def stream(self, message: str, on_token: Callable[[str], None],
               on_step: Optional[Callable[[dict], None]] = None,
               user: Optional[dict] = None) -> str:
        tok = self.session(user)
        req = self._req("/api/embed/chat/stream",
                        {"session_token": tok, "message": message},
                        extra={"Accept": "text/event-stream"})
        full, event, data = "", "message", ""
        with urllib.request.urlopen(req, timeout=None) as r:
            for raw in r:
                line = raw.decode("utf-8").rstrip("\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()
                elif line == "":  # frame boundary
                    if data and data != "[DONE]" and event != "done":
                        try:
                            j = json.loads(data)
                        except json.JSONDecodeError:
                            j = None
                        if event == "step" and on_step and isinstance(j, dict):
                            on_step(j)
                        elif isinstance(j, dict) and "delta" in j:
                            full += j["delta"]
                            on_token(j["delta"])
                    event, data = "message", ""
        return full

    # ---- internals --------------------------------------------------------

    def _req(self, path: str, body: dict, extra: Optional[dict] = None) -> urllib.request.Request:
        headers = {"Content-Type": "application/json"}
        if self.origin:
            headers["Origin"] = self.origin
        if extra:
            headers.update(extra)
        return urllib.request.Request(self.base + path,
                                      data=json.dumps(body).encode(),
                                      headers=headers, method="POST")

    def _post(self, path: str, body: dict) -> dict:
        try:
            with urllib.request.urlopen(self._req(path, body), timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode()
            try:
                detail = json.loads(detail).get("detail", detail)
            except json.JSONDecodeError:
                pass
            raise CityAgentError(f"HTTP {e.code} on {path}: {detail}") from None


if __name__ == "__main__":
    import os
    ca = CityAgent(
        os.environ.get("CITYAGENT_BASE", "http://localhost:8011"),
        os.environ.get("CITYAGENT_EMBED", "emb_rGd8VWW8DloS6WNNssvenA"),
        os.environ.get("CITYAGENT_PUBKEY", "pub_FWWyXah2Sv0iuN5f8TwQQH1v2LaoeIUT"),
        os.environ.get("CITYAGENT_EMBED_SECRET"),
        origin=os.environ.get("CITYAGENT_ORIGIN", "https://yourpharmacy.com"),
    )
    print("Blocking:", ca.ask("hello"))
    print("Streaming:", end=" ", flush=True)
    ca.stream("list substitutes for amoxicillin",
              on_token=lambda d: print(d, end="", flush=True),
              on_step=lambda s: print(f"\n[{s.get('icon','')} {s.get('label','')}]", flush=True))
    print()
