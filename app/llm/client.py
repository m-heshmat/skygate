"""Gemini client (uses the current google-genai SDK).

Uses Gemini's structured-output mode (`response_mime_type=application/json`)
so we never have to peel markdown code fences off the response. Falls back to
a regex extractor only if the model still returns garbage.
"""
from __future__ import annotations

import json
import re
import time
from typing import Iterable

from google import genai
from google.genai import types

from app.config import GEMINI_API_KEY, GEMINI_MODEL
from app.llm.prompts import build_followup_prompt, build_intent_prompt
from app.schemas import ToolRequest


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self) -> None:
        if not GEMINI_API_KEY:
            raise LLMError(
                "Missing GEMINI_API_KEY. Copy .env.example to .env and add your key."
            )
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model_name = GEMINI_MODEL
        self.config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        )

    # ---------- public -----------------------------------------------------

    def parse_intent(
        self,
        user_message: str,
        schemas: Iterable[dict],
        history: list[dict] | None = None,
    ) -> ToolRequest:
        prompt = build_intent_prompt(user_message, schemas, history=history)
        raw = self._generate(prompt, label="intent")
        parsed = _safe_json_loads(raw)
        if parsed is None:
            return ToolRequest.from_dict({
                "action": "unknown",
                "file": "unknown",
                "explanation": f"LLM returned non-JSON output: {raw[:200]}",
            })
        return ToolRequest.from_dict(parsed)

    def parse_followup(
        self,
        user_message: str,
        previous_request: dict,
        previous_result_summary: dict,
        schemas: Iterable[dict],
    ) -> dict:
        prompt = build_followup_prompt(
            user_message=user_message,
            previous_request=previous_request,
            previous_result_summary=previous_result_summary,
            schemas=schemas,
        )
        raw = self._generate(prompt, label="followup")
        parsed = _safe_json_loads(raw)
        if not isinstance(parsed, dict):
            return {"mode": "unclear", "reason": "follow-up parser returned invalid JSON"}
        mode = parsed.get("mode")
        if mode not in {"explain_previous", "refine_previous", "new_request", "unclear"}:
            mode = "unclear"
        return {
            "mode": mode,
            "reason": str(parsed.get("reason") or ""),
            "rewritten_request": parsed.get("rewritten_request"),
        }

    # ---------- internals --------------------------------------------------

    def _generate(self, prompt: str, label: str) -> str:
        """Generate content with one automatic retry on 429 RESOURCE_EXHAUSTED."""
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=self.config,
            )
        except Exception as e:
            delay = _extract_retry_delay(str(e))
            if delay is None:
                raise LLMError(f"Gemini {label} call failed: {type(e).__name__}: {e}") from e
            sleep_for = min(delay, 30) + 0.5
            time.sleep(sleep_for)
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=self.config,
                )
            except Exception as e2:
                raise LLMError(
                    f"Gemini {label} call failed after retry: {type(e2).__name__}: {e2}"
                ) from e2
        return (response.text or "").strip()


def _extract_retry_delay(message: str) -> float | None:
    """Pull the 'retryDelay' hint out of a 429 RESOURCE_EXHAUSTED error message."""
    if "RESOURCE_EXHAUSTED" not in message and "429" not in message:
        return None
    match = _RETRY_DELAY_RE.search(message)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _safe_json_loads(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(raw)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
