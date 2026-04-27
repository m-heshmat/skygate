"""Typed boundary between the LLM and the tool layer.

Everything that crosses the Python/LLM boundary is validated here so the rest
of the system can assume well-formed inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ActionType = Literal[
    "schema",       # describe the file's columns/dtypes/sample values
    "query",        # filter + sort + limit, return rows
    "aggregate",    # group_by + agg + derived columns + sort + limit
    "insert",       # add a new row
    "update",       # mutate matching rows
    "delete",       # remove matching rows
    "unknown",
]

FileType = Literal["real_estate", "marketing", "unknown"]

ALLOWED_OPS = {
    "=", "!=", ">", ">=", "<", "<=",
    "between", "in", "not_in",
    "contains", "starts_with", "ends_with",
    "is_null", "is_not_null",
}

OP_ALIASES = {
    "eq": "=",
    "ne": "!=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "ge": ">=",
    "lt": "<",
    "lte": "<=",
    "le": "<=",
}

ALLOWED_AGGS = {"sum", "mean", "median", "min", "max", "count", "nunique", "std"}

AGG_ALIASES = {
    "avg": "mean",
    "average": "mean",
    "stddev": "std",
    "stdev": "std",
    "distinct_count": "nunique",
    "count_distinct": "nunique",
    "minimum": "min",
    "maximum": "max",
    "total": "sum",
}


@dataclass
class Condition:
    """A single filter condition. The column is resolved later by the
    ColumnResolver, so the LLM may emit fuzzy names."""
    column: str
    op: str
    value: Any = None  # Not used for is_null / is_not_null

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not isinstance(self.column, str) or not self.column.strip():
            errors.append("condition.column must be a non-empty string")
        if self.op not in ALLOWED_OPS:
            errors.append(f"condition.op '{self.op}' not in {sorted(ALLOWED_OPS)}")
        if self.op == "between" and not (
            isinstance(self.value, (list, tuple)) and len(self.value) == 2
        ):
            errors.append("'between' requires value=[low, high]")
        if self.op in {"in", "not_in"} and not isinstance(self.value, (list, tuple)):
            errors.append(f"'{self.op}' requires value as a list")
        return errors


@dataclass
class FilterSpec:
    conditions: list[Condition] = field(default_factory=list)
    logic: Literal["and", "or"] = "and"

    @classmethod
    def from_dict(cls, raw: dict | None) -> "FilterSpec":
        if not raw:
            return cls()
        conds_raw = raw.get("conditions") or []
        conds = [
            Condition(
                column=c.get("column", ""),
                op=_normalize_op(c.get("op", "=")),
                value=c.get("value"),
            )
            for c in conds_raw
            if isinstance(c, dict)
        ]
        logic = raw.get("logic", "and")
        if logic not in {"and", "or"}:
            logic = "and"
        return cls(conditions=conds, logic=logic)

    def is_empty(self) -> bool:
        return len(self.conditions) == 0


@dataclass
class DerivedColumn:
    """A safe pandas .eval() expression. Column names with spaces must be
    wrapped in backticks, e.g. '`Revenue Generated` / `Amount Spent`'."""
    name: str
    expr: str


@dataclass
class Metric:
    agg: str            # sum/mean/...
    column: str         # source column (or derived alias)
    alias: str = ""     # output name; defaults to f"{agg}_{column}"


@dataclass
class SortSpec:
    column: str
    order: Literal["asc", "desc"] = "asc"


@dataclass
class AggregateSpec:
    derived: list[DerivedColumn] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    sort: list[SortSpec] = field(default_factory=list)
    limit: int | None = None
    filters: FilterSpec = field(default_factory=FilterSpec)  # pre-aggregate WHERE
    having: FilterSpec = field(default_factory=FilterSpec)   # post-aggregate HAVING


@dataclass
class ToolRequest:
    action: ActionType
    file: FileType
    filters: FilterSpec = field(default_factory=FilterSpec)
    sort: list[SortSpec] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)   # projection for query
    limit: int = 10
    data: dict[str, Any] = field(default_factory=dict)      # insert payload
    updates: dict[str, Any] = field(default_factory=dict)   # update assignments
    aggregate: AggregateSpec | None = None
    confirm: bool = False                              # require for write ops
    explanation: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "ToolRequest":
        action = raw.get("action") or "unknown"
        if action not in {
            "schema", "query", "aggregate", "insert", "update", "delete", "unknown"
        }:
            action = "unknown"

        file_ = raw.get("file") or "unknown"
        if file_ not in {"real_estate", "marketing", "unknown"}:
            file_ = "unknown"

        sort_raw = raw.get("sort") or []
        sort = [
            SortSpec(column=s.get("column", ""), order=s.get("order", "asc"))
            for s in sort_raw if isinstance(s, dict) and s.get("column")
        ]

        agg = None
        agg_raw = raw.get("aggregate")
        if isinstance(agg_raw, dict):
            agg = AggregateSpec(
                derived=[
                    DerivedColumn(name=d.get("name", ""), expr=d.get("expr", ""))
                    for d in (agg_raw.get("derived") or [])
                    if isinstance(d, dict) and d.get("name") and d.get("expr")
                ],
                group_by=[c for c in (agg_raw.get("group_by") or []) if isinstance(c, str)],
                metrics=[
                    Metric(
                        agg=_normalize_agg(m.get("agg", "sum")),
                        column=m.get("column", ""),
                        alias=m.get("alias", ""),
                    )
                    for m in (agg_raw.get("metrics") or [])
                    if isinstance(m, dict) and m.get("column")
                ],
                sort=[
                    SortSpec(column=s.get("column", ""), order=s.get("order", "asc"))
                    for s in (agg_raw.get("sort") or [])
                    if isinstance(s, dict) and s.get("column")
                ],
                limit=agg_raw.get("limit"),
                filters=FilterSpec.from_dict(agg_raw.get("filters")),
                having=FilterSpec.from_dict(agg_raw.get("having")),
            )

        limit = raw.get("limit")
        if not isinstance(limit, int) or limit <= 0:
            limit = 10

        return cls(
            action=action,
            file=file_,
            filters=FilterSpec.from_dict(raw.get("filters")),
            sort=sort,
            columns=[c for c in (raw.get("columns") or []) if isinstance(c, str)],
            limit=min(limit, 200),
            data=raw.get("data") or {},
            updates=raw.get("updates") or {},
            aggregate=agg,
            confirm=bool(raw.get("confirm", False)),
            explanation=str(raw.get("explanation") or ""),
        )


def _normalize_op(op: Any) -> str:
    if not isinstance(op, str):
        return "="
    key = op.strip().lower()
    return OP_ALIASES.get(key, op)


def _normalize_agg(agg: Any) -> str:
    if not isinstance(agg, str):
        return "sum"
    key = agg.strip().lower()
    return AGG_ALIASES.get(key, key)
