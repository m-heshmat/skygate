"""Excel I/O with safety guarantees:

  * Source files in data/ are treated as read-only inputs and copied into
    data/working/ on first access. Mutations only ever touch the working copy.
  * Saves are atomic (write to .tmp, fsync, os.replace) so a crash mid-write
    can't leave a half-written xlsx.
  * Loaded DataFrames are cached in memory and invalidated on save, so we
    don't re-read the file on every request.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pandas as pd

from app.config import SOURCE_FILES, WORKING_FILES
from app.tools.column_resolver import ColumnResolver


class ExcelStore:
    def __init__(self) -> None:
        self._cache: dict[str, pd.DataFrame] = {}
        self._ensure_working_copies()

    def _ensure_working_copies(self) -> None:
        for key, src in SOURCE_FILES.items():
            dst = WORKING_FILES[key]
            if not src.exists():
                raise FileNotFoundError(f"Source file missing: {src}")
            if not dst.exists():
                shutil.copy2(src, dst)

    def reset(self, file_key: str | None = None) -> None:
        """Restore the working copy from the original source. Used by tests."""
        keys = [file_key] if file_key else list(SOURCE_FILES.keys())
        for k in keys:
            shutil.copy2(SOURCE_FILES[k], WORKING_FILES[k])
            self._cache.pop(k, None)

    def _path(self, file_key: str) -> Path:
        if file_key not in WORKING_FILES:
            raise ValueError(
                f"Unknown file '{file_key}'. Known files: {list(WORKING_FILES)}"
            )
        return WORKING_FILES[file_key]

    def load(self, file_key: str) -> pd.DataFrame:
        if file_key in self._cache:
            return self._cache[file_key].copy()
        df = pd.read_excel(self._path(file_key))
        self._cache[file_key] = df
        return df.copy()

    def save(self, file_key: str, df: pd.DataFrame) -> None:
        path = self._path(file_key)
        with tempfile.NamedTemporaryFile(
            suffix=".xlsx", dir=path.parent, delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            df.to_excel(tmp_path, index=False)
            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
        self._cache[file_key] = df.copy()

    def resolver(self, file_key: str) -> ColumnResolver:
        return ColumnResolver(self.load(file_key).columns)

    def schema(self, file_key: str, sample_values: int = 5) -> dict:
        """Return columns + dtypes + a few sample values per column."""
        df = self.load(file_key)
        cols: list[dict] = []
        for c in df.columns:
            series = df[c]
            dtype = str(series.dtype)
            non_null = series.dropna()
            uniques = non_null.unique().tolist()
            samples: list = uniques[:sample_values]
            cols.append({
                "name": c,
                "dtype": dtype,
                "n_unique": int(non_null.nunique()),
                "n_null": int(series.isna().sum()),
                "samples": [_jsonable(v) for v in samples],
            })
        return {
            "file": file_key,
            "row_count": int(len(df)),
            "columns": cols,
        }


def _jsonable(v):
    """Make a value JSON-serialisable for prompt injection."""
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return None
    except Exception:
        pass
    return v
