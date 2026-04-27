"""JSONL transcript log. One file per session under logs/.

This is what you point at during the live demo when the interviewer asks
'how do I know what the LLM actually did?'.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from app.config import LOGS_DIR


class SessionLog:
    def __init__(self, session_id: str | None = None) -> None:
        sid = session_id or datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        self.path: Path = LOGS_DIR / f"session-{sid}.jsonl"

    def write(self, event: str, **payload) -> None:
        record = {
            "ts": time.time(),
            "event": event,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
