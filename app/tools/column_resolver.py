"""Resolve LLM-emitted column names to actual DataFrame columns.

The actual columns have spaces and Title Case (e.g. 'List Price'), but the LLM
will routinely emit 'list_price', 'price', or 'LIST_PRICE'. Silent skipping of
unknown columns is the single most common source of wrong answers, so this
resolver fails *loudly* when it cannot find a confident match.
"""
from __future__ import annotations

import difflib
import re
from typing import Iterable


def _normalise(name: str) -> str:
    """lower-case, strip non-alphanumerics. 'List_Price' -> 'listprice'."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


class ColumnResolutionError(ValueError):
    """Raised when a column reference cannot be confidently resolved."""

    def __init__(self, requested: str, available: Iterable[str], suggestion: str | None):
        self.requested = requested
        self.suggestion = suggestion
        avail = ", ".join(available)
        msg = f"Unknown column '{requested}'. Available columns: [{avail}]."
        if suggestion:
            msg += f" Did you mean '{suggestion}'?"
        super().__init__(msg)


class ColumnResolver:
    def __init__(self, columns: Iterable[str]):
        self._columns = list(columns)
        self._by_norm = {_normalise(c): c for c in self._columns}

    @property
    def columns(self) -> list[str]:
        return list(self._columns)

    def try_resolve(self, name: str) -> str | None:
        if not isinstance(name, str) or not name.strip():
            return None
        if name in self._columns:
            return name
        norm = _normalise(name)
        if norm in self._by_norm:
            return self._by_norm[norm]
        # Last-resort fuzzy match against normalised forms.
        candidates = difflib.get_close_matches(norm, self._by_norm.keys(), n=1, cutoff=0.85)
        if candidates:
            return self._by_norm[candidates[0]]
        return None

    def resolve(self, name: str) -> str:
        match = self.try_resolve(name)
        if match is not None:
            return match
        # Provide a suggestion even at lower confidence so the error is helpful.
        norm = _normalise(name)
        suggestions = difflib.get_close_matches(norm, self._by_norm.keys(), n=1, cutoff=0.5)
        suggestion = self._by_norm[suggestions[0]] if suggestions else None
        raise ColumnResolutionError(name, self._columns, suggestion)
