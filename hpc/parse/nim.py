"""Minimal NVIDIA NIM client (OpenAI-compatible API at build.nvidia.com).

The key is read from the NVIDIA_API_KEY environment variable (a GitHub Actions secret in CI)
or, locally, from the git-ignored .env file. It is never printed or logged.
"""
from __future__ import annotations

import json
import os
import time

import requests

from hpc.config import load_toml


class NimUnavailable(RuntimeError):
    pass


def api_key_from_env() -> str:
    """NVIDIA_API_KEY from the environment, else from the project's .env file (git-ignored).
    Never commit the key to config/*.toml: the repository is public."""
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if key:
        return key
    from hpc.config import ROOT
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "NVIDIA_API_KEY":
                return value.strip().strip('"').strip("'")
    return ""


class NimClient:
    def __init__(self, api_key: str | None = None, config: dict | None = None):
        cfg = config or load_toml("nim.toml")
        self.base_url = cfg["base_url"].rstrip("/")
        self.chat_model = cfg["chat_model"]
        self.embed_model = cfg.get("embed_model", "")
        self.chat_max_tokens = cfg.get("chat_max_tokens", 4000)
        self.timeout = cfg.get("timeout_seconds", 60)
        self.api_key = api_key or api_key_from_env()
        if not self.api_key:
            raise NimUnavailable("NVIDIA_API_KEY is not set (environment variable or .env file)")

    def _post(self, path: str, payload: dict, retries: int = 3) -> dict:
        for attempt in range(retries + 1):
            r = requests.post(
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
                json=payload, timeout=self.timeout,
            )
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt * 2)
                continue
            if r.status_code != 200:
                raise NimUnavailable(f"NIM HTTP {r.status_code}: {r.text[:200]}")
            return r.json()
        raise NimUnavailable("NIM retries exhausted")

    def chat_text(self, messages: list[dict], max_tokens: int | None = None, temperature: float = 0.2) -> str:
        """Plain-text reply (reasoning models keep their thinking in a separate field)."""
        data = self._post("/chat/completions", {
            "model": self.chat_model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens or self.chat_max_tokens,
        })
        choice = data["choices"][0]
        content = (choice["message"].get("content") or "").strip()
        if not content and choice.get("finish_reason") == "length":
            raise ValueError("model ran out of tokens before answering; raise chat_max_tokens")
        return content

    def chat_json(self, system: str, user: str, max_tokens: int | None = None) -> dict:
        """Ask for a JSON object; returns the parsed object. Reasoning models put their thinking
        in a separate field, so only the final `content` is parsed."""
        data = self._post("/chat/completions", {
            "model": self.chat_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0, "max_tokens": max_tokens or self.chat_max_tokens,
        })
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        if choice.get("finish_reason") == "length" and "}" not in content:
            raise ValueError("model ran out of tokens before answering; raise chat_max_tokens")
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end < 0:
            raise ValueError("model reply contains no JSON object")
        return json.loads(content[start:end + 1])
