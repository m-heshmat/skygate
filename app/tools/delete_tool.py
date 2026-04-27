"""Delete matching rows.

Same safety contract as UpdateTool: empty filters are rejected, and dry_run
returns what *would* be removed.
"""
from __future__ import annotations

from app.schemas import FilterSpec
from app.tools.column_resolver import ColumnResolver
from app.tools.excel_store import ExcelStore
from app.tools.filter_engine import apply_filter


class DeleteTool:
    def __init__(self, store: ExcelStore) -> None:
        self.store = store

    def run(self, file_key: str, filters: FilterSpec, dry_run: bool = False) -> dict:
        if filters.is_empty():
            raise ValueError(
                "Refusing to delete with no filters (would remove every row). "
                "Provide at least one filter condition."
            )

        df = self.store.load(file_key)
        resolver = ColumnResolver(df.columns)
        mask = apply_filter(df, filters, resolver)
        matched = int(mask.sum())

        if matched == 0:
            return {
                "file": file_key,
                "matched_rows": 0,
                "deleted_rows": 0,
                "message": "No rows matched the filter; nothing changed.",
            }

        if dry_run:
            preview = df.loc[mask].head(5)
            return {
                "dry_run": True,
                "file": file_key,
                "matched_rows": matched,
                "preview": preview.where(preview.notna(), None).to_dict(orient="records"),
            }

        df_out = df[~mask].reset_index(drop=True)
        self.store.save(file_key, df_out)
        return {
            "file": file_key,
            "matched_rows": matched,
            "deleted_rows": matched,
            "remaining_rows": int(len(df_out)),
        }
