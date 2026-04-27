"""Trivial wrapper around the store's schema() method, exposed as a tool so
the LLM can ask 'what columns does X have?' explicitly."""
from __future__ import annotations

from app.tools.excel_store import ExcelStore


class SchemaTool:
    def __init__(self, store: ExcelStore) -> None:
        self.store = store

    def run(self, file_key: str) -> dict:
        return self.store.schema(file_key, sample_values=8)
