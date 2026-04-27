"""Update matching rows.

Safety: refuses to run if FilterSpec is empty (would touch every row), and
supports a dry-run that returns what *would* change without writing.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.schemas import FilterSpec
from app.tools.column_resolver import ColumnResolver
from app.tools.excel_store import ExcelStore
from app.tools.filter_engine import apply_filter


def _coerce(series: pd.Series, value: Any) -> Any:
    if value is None:
        return value
    dtype = series.dtype
    try:
        if pd.api.types.is_datetime64_any_dtype(dtype):
            return pd.to_datetime(value)
        if pd.api.types.is_integer_dtype(dtype):
            return int(value)
        if pd.api.types.is_float_dtype(dtype):
            return float(value)
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"Cannot coerce value {value!r} to dtype {dtype} for column '{series.name}': {e}"
        )
    return value


class UpdateTool:
    def __init__(self, store: ExcelStore) -> None:
        self.store = store

    def run(
        self,
        file_key: str,
        filters: FilterSpec,
        updates: dict[str, Any],
        dry_run: bool = False,
    ) -> dict:
        if filters.is_empty():
            raise ValueError(
                "Refusing to update with no filters (would modify every row). "
                "Provide at least one filter condition."
            )
        if not updates:
            raise ValueError("update requires a non-empty 'updates' object")

        df = self.store.load(file_key)
        resolver = ColumnResolver(df.columns)
        mask = apply_filter(df, filters, resolver)
        matched = int(mask.sum())

        if matched == 0:
            return {
                "file": file_key,
                "matched_rows": 0,
                "updated_rows": 0,
                "message": "No rows matched the filter; nothing changed.",
            }

        resolved_updates: dict[str, Any] = {}
        for raw_key, raw_val in updates.items():
            col = resolver.resolve(raw_key)
            resolved_updates[col] = _coerce(df[col], raw_val)

        if dry_run:
            preview = df.loc[mask].head(5)
            return {
                "dry_run": True,
                "file": file_key,
                "matched_rows": matched,
                "updates": resolved_updates,
                "preview_before": preview.where(preview.notna(), None).to_dict(orient="records"),
            }

        for col, val in resolved_updates.items():
            df.loc[mask, col] = val

        self.store.save(file_key, df)
        return {
            "file": file_key,
            "matched_rows": matched,
            "updated_rows": matched,
            "updates": resolved_updates,
        }
