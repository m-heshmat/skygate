"""Group-by + aggregation + derived columns + sort + limit.

Derived columns use pandas.DataFrame.eval(), which is restricted to arithmetic
and column references and cannot run arbitrary Python. This is the safe way
to let the LLM compose metrics like ROI = Revenue / Spend.
"""
from __future__ import annotations

import re

import pandas as pd

from app.config import MAX_PREVIEW_ROWS
from app.schemas import AggregateSpec, ALLOWED_AGGS
from app.tools.column_resolver import ColumnResolver
from app.tools.excel_store import ExcelStore
from app.tools.filter_engine import apply_filter


_BACKTICK_RE = re.compile(r"`([^`]+)`")


def _rewrite_expr(expr: str, resolver: ColumnResolver) -> str:
    """Resolve every `Backticked Column` reference in a derived expression."""

    def repl(match: re.Match) -> str:
        col = resolver.resolve(match.group(1))
        return f"`{col}`"

    return _BACKTICK_RE.sub(repl, expr)


class AggregateTool:
    def __init__(self, store: ExcelStore) -> None:
        self.store = store

    def run(self, file_key: str, spec: AggregateSpec) -> dict:
        df = self.store.load(file_key)
        resolver = ColumnResolver(df.columns)

        if not spec.metrics:
            raise ValueError("aggregate requires at least one metric")

        mask = apply_filter(df, spec.filters, resolver)
        df = df[mask].copy()

        # 1. Derived columns (computed BEFORE grouping so they can be aggregated).
        derived_resolver = resolver
        for d in spec.derived:
            if not d.name or not d.expr:
                continue
            safe_expr = _rewrite_expr(d.expr, resolver)
            try:
                df[d.name] = df.eval(safe_expr, engine="python")
            except Exception as e:
                raise ValueError(f"Failed to evaluate derived column '{d.name}': {e}")
            derived_resolver = ColumnResolver(df.columns)

        # 2. Group-by columns.
        group_cols = [derived_resolver.resolve(g) for g in spec.group_by]

        # 3. Build the aggregation dict.
        agg_dict: dict[str, list[str]] = {}
        for m in spec.metrics:
            if m.agg not in ALLOWED_AGGS:
                raise ValueError(f"Aggregation '{m.agg}' not allowed")
            col = derived_resolver.resolve(m.column)
            agg_dict.setdefault(col, []).append(m.agg)

        if group_cols:
            grouped = df.groupby(group_cols, dropna=False).agg(agg_dict)
        else:
            grouped = df.agg(agg_dict)
            if isinstance(grouped, pd.Series):
                grouped = grouped.to_frame().T

        # Flatten MultiIndex columns -> single level with the alias if provided.
        flat_cols: list[str] = []
        for source_col, aggs in agg_dict.items():
            for a in aggs:
                alias = next(
                    (m.alias for m in spec.metrics
                     if derived_resolver.resolve(m.column) == source_col
                     and m.agg == a and m.alias),
                    f"{a}_{source_col}",
                )
                flat_cols.append(alias)
        grouped.columns = flat_cols

        if group_cols:
            grouped = grouped.reset_index()

        # 4. HAVING filter (post-aggregation, supports alias columns like total_revenue).
        if not spec.having.is_empty():
            having_resolver = ColumnResolver(grouped.columns)
            having_mask = apply_filter(grouped, spec.having, having_resolver)
            grouped = grouped[having_mask].copy()

        # 5. Sort.
        if spec.sort:
            by, ascending = [], []
            sort_resolver = ColumnResolver(grouped.columns)
            for s in spec.sort:
                by.append(sort_resolver.resolve(s.column))
                ascending.append(s.order != "desc")
            grouped = grouped.sort_values(by=by, ascending=ascending, kind="mergesort")

        # 6. Limit.
        limit = spec.limit or MAX_PREVIEW_ROWS
        limit = max(1, min(int(limit), MAX_PREVIEW_ROWS))
        out = grouped.head(limit)

        return {
            "file": file_key,
            "group_by": group_cols,
            "metrics": [
                {"alias": a, "agg": m.agg, "column": derived_resolver.resolve(m.column)}
                for a, m in zip(flat_cols, spec.metrics)
            ],
            "having": {
                "logic": spec.having.logic,
                "conditions": [
                    {"column": c.column, "op": c.op, "value": c.value}
                    for c in spec.having.conditions
                ],
            } if not spec.having.is_empty() else None,
            "matched_rows": int(len(grouped)),
            "returned_rows": int(len(out)),
            "rows": out.where(out.notna(), None).to_dict(orient="records"),
        }
