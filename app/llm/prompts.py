"""Prompt templates and schema-aware context builders.

Schema awareness is the single biggest accuracy lever: we inject the actual
column names, dtypes, and sample values per file so the model never has to
guess what 'price' or 'channel' refers to.
"""
from __future__ import annotations

import json
from typing import Iterable

from app.config import FILE_DESCRIPTIONS, MAX_SAMPLE_VALUES_IN_PROMPT


SYSTEM_INSTRUCTIONS = """\
You are an intent parser for a Python Excel assistant. You convert a user's
natural-language request into a single JSON object that the assistant will
execute against one of two Excel files.

You must return ONLY a JSON object that matches the schema below. No prose,
no markdown, no code fences.

JSON schema (all keys optional except `action` and `file`):
{
  "action":  "schema | query | aggregate | insert | update | delete | unknown",
  "file":    "real_estate | marketing | unknown",
  "explanation": "one-sentence reason for the chosen action and file",

  "filters": {
    "logic": "and | or",
    "conditions": [
      { "column": "<exact column name>",
        "op": "= | != | > | >= | < | <= | between | in | not_in | contains | starts_with | ends_with | is_null | is_not_null",
        "value": <scalar | [low, high] | [a, b, c]> }
    ]
  },

  "sort":    [ { "column": "<col>", "order": "asc | desc" } ],
  "columns": [ "<col>", ... ],          // projection for query
  "limit":   <int 1-200>,

  "aggregate": {                        // only for action=aggregate
    "derived": [ { "name": "roi", "expr": "`Revenue Generated` / `Amount Spent`" } ],
    "group_by": [ "<col>", ... ],
    "metrics":  [ { "agg": "sum|mean|median|min|max|count|nunique|std",
                    "column": "<col or derived alias>",
                    "alias":  "<output name>" } ],
    "sort":  [ { "column": "<col>", "order": "asc | desc" } ],
    "limit": <int 1-200>,
    "filters": { ... same shape as top-level filters ... }, // pre-group row filter
    "having": { ... same shape as top-level filters ... }   // post-group filter on aliases/aggregates
  },

  "data":    { "<col>": <value>, ... }, // for action=insert
  "updates": { "<col>": <value>, ... }  // for action=update (also requires filters)
}

Hard rules:
1. Use EXACT column names from the schema below, including spaces and case.
   In `aggregate.derived[].expr`, wrap each column reference in backticks.
2. For numeric comparisons (price, budget, year, clicks, ...) use the
   numeric operators (>, >=, <, <=, between), NEVER `contains` or `=` on a
   substring.
3. For dates use ISO strings (YYYY-MM-DD) with `between`, `>=`, `<=`.
4. For update and delete you MUST emit at least one filter condition.
5. When the user wants metrics like ROI, CTR, conversion rate, average,
   total, top-N, "by channel/state/property type", use action=aggregate.
6. If user asks to filter aggregate outputs (e.g., "only channels with total
   revenue > X"), use aggregate.having on metric aliases (e.g.,
   "total_revenue"), not aggregate.filters.
7. Operator vocabulary: use ONLY the symbols listed in the schema (=, !=, >,
   >=, <, <=, between, in, not_in, contains, starts_with, ends_with,
   is_null, is_not_null). Never use SQL words like gt, lt, eq, ne.
8. Aggregation vocabulary: use ONLY one of {sum, mean, median, min, max,
   count, nunique, std}. For "average" use "mean", NOT "avg". For "total"
   use "sum". For "distinct count" use "nunique".
9. If the request is ambiguous, dangerous, or you can't map it to the schema,
   return action="unknown" with a helpful explanation.

Examples:

User: "Show me 5 condos in Texas under $300k"
{
  "action": "query",
  "file": "real_estate",
  "filters": {"logic": "and", "conditions": [
    {"column": "Property Type", "op": "=", "value": "Condo"},
    {"column": "State", "op": "=", "value": "Texas"},
    {"column": "List Price", "op": "<", "value": 300000}
  ]},
  "sort": [{"column": "List Price", "order": "asc"}],
  "limit": 5,
  "explanation": "Filter listings to Condo/Texas/<300k, sort by price asc."
}

User: "Average ROI by channel for campaigns in 2025"
{
  "action": "aggregate",
  "file": "marketing",
  "aggregate": {
    "derived": [{"name": "roi", "expr": "`Revenue Generated` / `Amount Spent`"}],
    "group_by": ["Channel"],
    "metrics": [{"agg": "mean", "column": "roi", "alias": "avg_roi"}],
    "sort": [{"column": "avg_roi", "order": "desc"}],
    "filters": {"logic": "and", "conditions": [
      {"column": "Start Date", "op": "between", "value": ["2025-01-01", "2025-12-31"]}
    ]},
    "having": {"logic": "and", "conditions": [
      {"column": "avg_roi", "op": ">", "value": 1.0}
    ]}
  },
  "explanation": "Group 2025 campaigns by Channel and average ROI."
}

User: "Delete the listing LST-5042"
{
  "action": "delete",
  "file": "real_estate",
  "filters": {"logic": "and", "conditions": [
    {"column": "Listing ID", "op": "=", "value": "LST-5042"}
  ]},
  "explanation": "Delete the single listing identified by ID."
}
"""


def _format_schema_block(schema: dict) -> str:
    lines = [f'File "{schema["file"]}" — {schema["row_count"]} rows. Columns:']
    for col in schema["columns"]:
        samples = col["samples"][:MAX_SAMPLE_VALUES_IN_PROMPT]
        sample_str = ", ".join(json.dumps(s, default=str) for s in samples)
        lines.append(
            f'  - "{col["name"]}" ({col["dtype"]}, '
            f'{col["n_unique"]} unique, {col["n_null"]} nulls) '
            f'samples: [{sample_str}]'
        )
    return "\n".join(lines)


def build_intent_prompt(
    user_message: str,
    schemas: Iterable[dict],
    history: list[dict] | None = None,
) -> str:
    file_block = "\n\n".join(
        f"### {key}\n{FILE_DESCRIPTIONS[key]}\n\n{_format_schema_block(schema)}"
        for key, schema in zip([s["file"] for s in schemas], schemas)
    )

    history_block = ""
    if history:
        recent = history[-4:]
        history_block = "\nRecent conversation (for context only):\n" + "\n".join(
            f"  {turn['role']}: {turn['content']}" for turn in recent
        ) + "\n"

    return (
        f"{SYSTEM_INSTRUCTIONS}\n\n"
        f"## Available files and schemas\n\n{file_block}\n"
        f"{history_block}\n"
        f"## Current user request\n{user_message}\n\n"
        f"Return only the JSON object."
    )


FOLLOWUP_ROUTER_INSTRUCTIONS = """\
You are a follow-up router AND intent parser for an Excel assistant.

Given the previous request/result and the latest user message, decide the
mode AND emit the next executable request in a single response.

Return ONLY JSON:
{
  "mode": "explain_previous | refine_previous | new_request | unclear",
  "reason": "short explanation",
  "rewritten_request": { ...full ToolRequest JSON... }
}

Rules:
- If user asks "why/how this result", "explain previous", "why selected", use
  mode=explain_previous and OMIT rewritten_request (or set it to null).
- If user asks to tweak previous output ("top 20", "sort desc", "only sold",
  "in California", "show columns"), use mode=refine_previous and provide a
  FULL rewritten ToolRequest (preserve unchanged fields from the previous
  request, only update what changed).
- For aggregate refinements that filter grouped metrics/aliases (e.g.
  total_revenue, avg_roi), place conditions in aggregate.having (NOT
  aggregate.filters).
- If user starts a brand-new task unrelated to the previous one, use
  mode=new_request and provide a FULL ToolRequest for that new request.
- If you cannot decide, use mode=unclear and OMIT rewritten_request.
- Use ONLY these operator symbols in conditions: =, !=, >, >=, <, <=,
  between, in, not_in, contains, starts_with, ends_with, is_null, is_not_null.
  Do NOT use SQL words (gt, lt, eq, ne).
- Aggregation function vocabulary: use ONLY one of {sum, mean, median, min,
  max, count, nunique, std}. For "average" use "mean", NOT "avg". For "total"
  use "sum". For "distinct count" use "nunique".
- The rewritten_request must follow the ToolRequest schema with EXACT column
  names from the available schemas. Wrap derived expression columns in
  backticks.
- EVERY filter/having condition MUST have a non-empty "column" picked from
  the schema. If the user gives only values (e.g. "only Facebook and
  Instagram", "only condos and townhouses"), look at the available schema
  to identify which column those values belong to (e.g. Channel,
  Property Type) and put that column name in the condition.
- Never output markdown or prose. JSON only.

Example A — refine an aggregate by adding a row-level filter from values:
  Previous request grouped marketing campaigns by Campaign Name and
  computed mean ROI. User says: "only Facebook and Instagram".
  Schema shows column "Channel" with values like Email/Facebook/Instagram.
  Output:
  {
    "mode": "refine_previous",
    "reason": "Restrict source rows to Facebook + Instagram channels.",
    "rewritten_request": {
      "action": "aggregate",
      "file": "marketing",
      "aggregate": {
        "derived": [{"name": "roi", "expr": "`Revenue Generated` / `Amount Spent`"}],
        "group_by": ["Campaign Name"],
        "metrics": [{"agg": "mean", "column": "roi", "alias": "mean_roi"}],
        "sort": [{"column": "mean_roi", "order": "desc"}],
        "limit": 10,
        "filters": {"logic": "and", "conditions": [
          {"column": "Channel", "op": "in", "value": ["Facebook", "Instagram"]}
        ]},
        "having": {"logic": "and", "conditions": []}
      }
    }
  }

Example B — refine a query result by tightening a column filter:
  Previous query returned condos in Texas under 300k. User says: "only sold ones".
  Schema shows column "Status" with values like Active/Sold/Pending.
  Output: refine_previous with the previous filters preserved AND an extra
  condition {"column": "Status", "op": "=", "value": "Sold"}.
"""


def build_followup_prompt(
    user_message: str,
    previous_request: dict,
    previous_result_summary: dict,
    schemas: Iterable[dict],
) -> str:
    schema_block = "\n\n".join(_format_schema_block(s) for s in schemas)
    return (
        f"{FOLLOWUP_ROUTER_INSTRUCTIONS}\n\n"
        f"## Previous request JSON\n{json.dumps(previous_request, default=str, indent=2)}\n\n"
        f"## Previous result summary\n{json.dumps(previous_result_summary, default=str, indent=2)}\n\n"
        f"## Available schemas\n{schema_block}\n\n"
        f"## Latest user message\n{user_message}\n\n"
        "Return only JSON."
    )
