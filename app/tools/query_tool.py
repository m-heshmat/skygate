"""Filter + sort + project + limit. Returns rows."""
from __future__ import annotations

import pandas as pd

from app.config import DEFAULT_PREVIEW_ROWS, MAX_PREVIEW_ROWS
from app.schemas import FilterSpec, SortSpec
from app.tools.column_resolver import ColumnResolver
from app.tools.excel_store import ExcelStore
from app.tools.filter_engine import apply_filter


class QueryTool:
    def __init__(self, store: ExcelStore) -> None:
        self.store = store

    def run(
        self,
        file_key: str,
        filters: FilterSpec,
        sort: list[SortSpec],
        columns: list[str],
        limit: int = DEFAULT_PREVIEW_ROWS,
    ) -> dict:
        df = self.store.load(file_key)
        resolver = ColumnResolver(df.columns)

        mask = apply_filter(df, filters, resolver)
        out = df[mask]

        if sort:
            by, ascending = [], []
            for s in sort:
                by.append(resolver.resolve(s.column))
                ascending.append(s.order != "desc")
            out = out.sort_values(by=by, ascending=ascending, kind="mergesort")

        projection = [resolver.resolve(c) for c in columns] if columns else list(df.columns)

        limit = max(1, min(limit or DEFAULT_PREVIEW_ROWS, MAX_PREVIEW_ROWS))
        preview = out[projection].head(limit)

        return {
            "file": file_key,
            "matched_rows": int(len(out)),
            "returned_rows": int(len(preview)),
            "columns": projection,
            "rows": _records(preview),
        }


def _records(df: pd.DataFrame) -> list[dict]:
    return df.where(df.notna(), None).to_dict(orient="records")
