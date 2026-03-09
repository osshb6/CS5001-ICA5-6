from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import requests


@dataclass
class OllamaConfig:
    base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model: str = os.getenv("OLLAMA_MODEL", "devstral-small-2:24b-cloud")
    timeout_seconds: int = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))


class OllamaClient:
    def __init__(self, config: OllamaConfig | None = None, logger: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.config = config or OllamaConfig()
        self.logger = logger

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        role: str = "Agent",
        step: str = "chat",
    ) -> str:
        url = f"{self.config.base_url.rstrip('/')}/api/chat"
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }

        self._log(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "role": role,
                "step": f"{step}_request",
                "model": self.config.model,
                "system_prompt": system_prompt[:1200],
                "user_prompt": user_prompt[:2500],
            }
        )

        try:
            response = requests.post(url, json=payload, timeout=self.config.timeout_seconds)
            response.raise_for_status()
            data = response.json()
            message = data.get("message") or {}
            content = str(message.get("content", "")).strip()

            self._log(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "role": role,
                    "step": step,
                    "model": self.config.model,
                    "response": content[:3000],
                }
            )
            return content
        except Exception as exc:
            self._log(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "role": role,
                    "step": f"{step}_error",
                    "model": self.config.model,
                    "error": str(exc),
                }
            )
            raise

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        role: str = "Agent",
        step: str = "chat_json",
    ) -> dict[str, Any]:
        raw = self.chat(system_prompt, user_prompt, temperature=temperature, role=role, step=step)
        parsed = self._extract_json(raw)
        self._log(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "role": role,
                "step": f"{step}_parsed",
                "model": self.config.model,
                "response_json": parsed,
            }
        )
        return parsed

    def _log(self, record: dict[str, Any]) -> None:
        if self.logger:
            try:
                self.logger(record)
            except Exception:
                pass

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        if not raw.strip():
            raise ValueError("Empty response from Ollama.")

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise ValueError("No JSON object found in Ollama response.")

        parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            raise ValueError("Parsed JSON is not an object.")
        return parsed
