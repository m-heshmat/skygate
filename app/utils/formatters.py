"""Pretty-print tool results as tables for the CLI."""
from __future__ import annotations

from tabulate import tabulate


def format_result(action: str, result: dict) -> str:
    if "error" in result:
        return f"Error: {result['error']}\n" + (
            f"  details: {result['details']}\n" if result.get("details") else ""
        )

    if action == "schema":
        rows = [
            [c["name"], c["dtype"], c["n_unique"], c["n_null"], _truncate(c["samples"])]
            for c in result["columns"]
        ]
        return (
            f"File: {result['file']} ({result['row_count']} rows)\n"
            + tabulate(
                rows,
                headers=["column", "dtype", "n_unique", "n_null", "samples"],
                tablefmt="github",
            )
        )

    if action in {"query", "aggregate"}:
        rows = result.get("rows") or []
        header = (
            f"{result.get('matched_rows', len(rows))} rows matched, "
            f"showing {result.get('returned_rows', len(rows))}."
        )
        if not rows:
            return header + "\n(no rows)"
        cols = result.get("columns") or list(rows[0].keys())
        table = tabulate(
            [[_fmt(r.get(c)) for c in cols] for r in rows],
            headers=cols,
            tablefmt="github",
        )
        return header + "\n" + table

    if action == "insert":
        if result.get("dry_run"):
            return "DRY RUN — would insert:\n" + _kv_table(result["would_insert"])
        return (
            f"Inserted into {result['file']}. "
            f"Total rows now: {result['new_row_count']}.\n"
            + _kv_table(result["inserted"])
        )

    if action == "update":
        head = (
            f"DRY RUN — would update {result['matched_rows']} rows.\n"
            if result.get("dry_run")
            else f"Updated {result.get('updated_rows', 0)} rows in {result['file']}.\n"
        )
        body = "Updates:\n" + _kv_table(result.get("updates") or {})
        if result.get("preview_before"):
            cols = list(result["preview_before"][0].keys())
            body += "\nPreview (before):\n" + tabulate(
                [[_fmt(r.get(c)) for c in cols] for r in result["preview_before"]],
                headers=cols,
                tablefmt="github",
            )
        return head + body

    if action == "delete":
        if result.get("dry_run"):
            cols = list(result["preview"][0].keys()) if result.get("preview") else []
            preview = (
                tabulate(
                    [[_fmt(r.get(c)) for c in cols] for r in result["preview"]],
                    headers=cols,
                    tablefmt="github",
                )
                if cols
                else ""
            )
            return f"DRY RUN — would delete {result['matched_rows']} rows.\n{preview}"
        return (
            f"Deleted {result.get('deleted_rows', 0)} rows from {result['file']}. "
            f"{result.get('remaining_rows', '?')} rows remain."
        )

    if action == "explain":
        lines = [result.get("reason", "Explanation for previous result.")]
        for key in (
            "file",
            "filters_logic",
            "conditions",
            "sort",
            "group_by",
            "metrics",
            "derived",
            "having",
            "limit",
            "matched_rows",
            "returned_rows",
        ):
            if key not in result or result[key] is None:
                continue
            value = result[key]
            if isinstance(value, list):
                lines.append(f"{key}:")
                lines.extend(f"  - {_fmt(v)}" for v in value)
            else:
                lines.append(f"{key}: {_fmt(value)}")
        return "\n".join(lines)

    if action == "clarify":
        return result.get("message", "Please clarify your request.")

    return str(result)


def _kv_table(d: dict) -> str:
    return tabulate(
        [[k, _fmt(v)] for k, v in d.items()],
        headers=["column", "value"],
        tablefmt="github",
    )


def _truncate(values, n: int = 4) -> str:
    s = ", ".join(str(v) for v in list(values)[:n])
    if len(values) > n:
        s += ", ..."
    return s


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if v.is_integer():
            return f"{int(v)}"
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)
