"""Insert a single row.

Validations:
  * Resolves user-provided keys to actual columns.
  * Coerces values to the column dtype, surfacing a clear error on mismatch.
  * Auto-generates IDs that follow the existing pattern (e.g. LST-####)
    if the LLM didn't supply one.
  * Refuses to insert a duplicate ID.
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

from app.tools.column_resolver import ColumnResolver
from app.tools.excel_store import ExcelStore


_ID_COLUMNS = {"real_estate": "Listing ID", "marketing": "Campaign ID"}


def _next_id(existing: pd.Series) -> str | None:
    """Increment the trailing integer of a 'PREFIX-####' style ID column."""
    sample = existing.dropna().astype(str)
    if sample.empty:
        return None
    match = re.match(r"([A-Za-z]+-)(\d+)$", sample.iloc[0])
    if not match:
        return None
    prefix = match.group(1)
    nums = sample.str.extract(r"-(\d+)$", expand=False).dropna().astype(int)
    return f"{prefix}{int(nums.max()) + 1}"


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


class InsertTool:
    def __init__(self, store: ExcelStore) -> None:
        self.store = store

    def run(self, file_key: str, data: dict[str, Any], dry_run: bool = False) -> dict:
        if not data:
            raise ValueError("insert requires a non-empty 'data' object")

        df = self.store.load(file_key)
        resolver = ColumnResolver(df.columns)

        new_row: dict[str, Any] = {c: None for c in df.columns}

        for raw_key, raw_val in data.items():
            col = resolver.resolve(raw_key)
            new_row[col] = _coerce(df[col], raw_val)

        # Auto-generate IDs if the file is one of ours and the user omitted it.
        id_col = _ID_COLUMNS.get(file_key)
        if id_col and id_col in df.columns and not new_row.get(id_col):
            generated = _next_id(df[id_col])
            if generated:
                new_row[id_col] = generated

        # Reject duplicate IDs.
        if id_col and new_row.get(id_col) in set(df[id_col].dropna().astype(str)):
            raise ValueError(f"Row with {id_col}={new_row[id_col]!r} already exists")

        if dry_run:
            return {
                "dry_run": True,
                "file": file_key,
                "would_insert": _jsonable(new_row),
            }

        df_out = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        self.store.save(file_key, df_out)

        return {
            "file": file_key,
            "inserted": _jsonable(new_row),
            "new_row_count": int(len(df_out)),
        }


def _jsonable(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, pd.Timestamp):
            out[k] = v.isoformat()
        else:
            out[k] = None if pd.isna(v) else v
    return out
