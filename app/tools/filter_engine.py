"""Operator-aware filter engine.

Replaces the original equality/substring-only logic. Critically:
  * unknown columns raise rather than silently disappearing,
  * empty filter sets for write operations are caught higher up,
  * the row mask is always a real pandas Series so '~mask' is well-defined.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.schemas import Condition, FilterSpec
from app.tools.column_resolver import ColumnResolver


def _coerce_value(series: pd.Series, value: Any) -> Any:
    """Best-effort coercion of an LLM-emitted value to the column's dtype."""
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
        if pd.api.types.is_bool_dtype(dtype):
            if isinstance(value, str):
                return value.strip().lower() in {"true", "1", "yes", "y"}
            return bool(value)
    except (ValueError, TypeError):
        # Fall through and return the raw value; the comparison will surface
        # any incompatibility with a clear error.
        pass
    return value


def _condition_mask(df: pd.DataFrame, cond: Condition, resolver: ColumnResolver) -> pd.Series:
    errors = cond.validate()
    if errors:
        raise ValueError("; ".join(errors))

    column = resolver.resolve(cond.column)
    series = df[column]
    op = cond.op
    val = cond.value

    if op == "is_null":
        return series.isna()
    if op == "is_not_null":
        return series.notna()

    if op == "between":
        low, high = _coerce_value(series, val[0]), _coerce_value(series, val[1])
        return series.between(low, high)

    if op in {"in", "not_in"}:
        coerced = [_coerce_value(series, v) for v in val]
        result = series.isin(coerced)
        return ~result if op == "not_in" else result

    if op in {"contains", "starts_with", "ends_with"}:
        s = series.astype(str)
        v = str(val)
        if op == "contains":
            return s.str.contains(v, case=False, na=False, regex=False)
        if op == "starts_with":
            return s.str.lower().str.startswith(v.lower())
        if op == "ends_with":
            return s.str.lower().str.endswith(v.lower())

    coerced = _coerce_value(series, val)
    if op == "=":
        # Strings: case-insensitive equality (still exact, not substring).
        if pd.api.types.is_object_dtype(series) and isinstance(coerced, str):
            return series.astype(str).str.casefold() == coerced.casefold()
        return series == coerced
    if op == "!=":
        if pd.api.types.is_object_dtype(series) and isinstance(coerced, str):
            return series.astype(str).str.casefold() != coerced.casefold()
        return series != coerced
    if op == ">":
        return series > coerced
    if op == ">=":
        return series >= coerced
    if op == "<":
        return series < coerced
    if op == "<=":
        return series <= coerced

    raise ValueError(f"Unsupported operator '{op}'")


def apply_filter(df: pd.DataFrame, spec: FilterSpec, resolver: ColumnResolver) -> pd.Series:
    """Return a boolean Series of len(df). Always a Series, never a scalar."""
    if spec.is_empty():
        return pd.Series(True, index=df.index)

    masks = [_condition_mask(df, c, resolver) for c in spec.conditions]
    combined = masks[0]
    for m in masks[1:]:
        combined = (combined & m) if spec.logic == "and" else (combined | m)
    return combined.fillna(False)
