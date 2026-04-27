"""Top-level orchestrator.

Flow per user message:

  1. Load schemas for both files (cheap, cached).
  2. Ask the LLM to translate the message into a ToolRequest, with the
     schemas + last few turns injected for context.
  3. Validate the request (action/file recognised, write ops have filters).
  4. Dispatch to the correct tool. Write actions go through dry_run first
     unless the request carries `confirm=True`.
  5. Log everything to the session JSONL.
"""
from __future__ import annotations

from typing import Any

from app.llm.client import LLMClient, LLMError
from app.schemas import ToolRequest
from app.tools.aggregate_tool import AggregateTool
from app.tools.column_resolver import ColumnResolutionError
from app.tools.delete_tool import DeleteTool
from app.tools.excel_store import ExcelStore
from app.tools.insert_tool import InsertTool
from app.tools.query_tool import QueryTool
from app.tools.schema_tool import SchemaTool
from app.tools.update_tool import UpdateTool
from app.utils.logging_setup import SessionLog


WRITE_ACTIONS = {"insert", "update", "delete"}


class ExcelAssistant:
    def __init__(self) -> None:
        self.store = ExcelStore()
        self.llm = LLMClient()
        self.log = SessionLog()
        self.history: list[dict] = []
        self._pending_confirmation: ToolRequest | None = None
        self._last_request: ToolRequest | None = None
        self._last_result: dict | None = None

        self.schema_tool = SchemaTool(self.store)
        self.tools: dict[str, Any] = {
            "query": QueryTool(self.store),
            "aggregate": AggregateTool(self.store),
            "insert": InsertTool(self.store),
            "update": UpdateTool(self.store),
            "delete": DeleteTool(self.store),
        }

    # ---------------------------------------------------------------- public

    def handle_message(self, user_message: str, auto_confirm: bool = False) -> dict:
        msg = user_message.strip()
        self.history.append({"role": "user", "content": msg})

        # Two-step write confirmation: a previous turn returned a dry-run for a
        # write action; this turn the user can say yes/confirm to commit.
        if self._pending_confirmation is not None:
            if msg.lower() in {"yes", "y", "confirm", "do it", "go ahead"}:
                req = self._pending_confirmation
                self._pending_confirmation = None
                req.confirm = True
                return self._dispatch(req, requested_message="<confirmation>")
            if msg.lower() in {"no", "n", "cancel", "abort"}:
                self._pending_confirmation = None
                response = {"action": "cancelled", "message": "Write cancelled."}
                self._record(response)
                return response
            # Anything else: drop the pending op and continue normally.
            self._pending_confirmation = None

        try:
            schemas = [self.store.schema(k) for k in ("real_estate", "marketing")]
            followup = self._route_followup(msg, schemas)
            if followup is not None:
                return followup
            # No prior context (or router asked us to fall back): single intent call.
            request = self.llm.parse_intent(msg, schemas, history=self.history)
        except LLMError as e:
            return self._record({"action": "error", "error": str(e)})

        self.log.write("intent", user=msg, request=request.__dict__)

        if request.action in WRITE_ACTIONS and not auto_confirm and not request.confirm:
            self._pending_confirmation = request
            try:
                preview = self._run_tool(request, dry_run=True)
            except (ValueError, ColumnResolutionError) as e:
                self._pending_confirmation = None
                return self._record({"action": "error", "error": str(e)})
            preview["__pending_action__"] = request.action
            preview["__followup__"] = "Reply 'yes' to commit, 'no' to cancel."
            return self._record(preview, action=request.action)

        return self._dispatch(request, requested_message=msg)

    def reset_data(self) -> None:
        """Restore working copies of both files from the originals."""
        self.store.reset()

    # --------------------------------------------------------------- private

    def _dispatch(self, request: ToolRequest, requested_message: str) -> dict:
        try:
            result = self._run_tool(request, dry_run=False)
        except (ValueError, ColumnResolutionError) as e:
            return self._record({"action": "error", "error": str(e)})
        except Exception as e:
            return self._record(
                {"action": "error", "error": f"{type(e).__name__}: {e}"}
            )
        self._last_request = request
        self._last_result = result
        return self._record(result, action=request.action)

    def _run_tool(self, request: ToolRequest, dry_run: bool) -> dict:
        if request.action == "unknown" or request.file == "unknown":
            return {
                "action": "unknown",
                "error": "Could not map request to a known file/action.",
                "explanation": request.explanation,
            }

        if request.action == "schema":
            return self.schema_tool.run(request.file)

        if request.action == "query":
            return self.tools["query"].run(
                request.file,
                request.filters,
                request.sort,
                request.columns,
                request.limit,
            )

        if request.action == "aggregate":
            if not request.aggregate:
                raise ValueError("aggregate action requires an 'aggregate' block")
            return self.tools["aggregate"].run(request.file, request.aggregate)

        if request.action == "insert":
            return self.tools["insert"].run(request.file, request.data, dry_run=dry_run)

        if request.action == "update":
            return self.tools["update"].run(
                request.file, request.filters, request.updates, dry_run=dry_run
            )

        if request.action == "delete":
            return self.tools["delete"].run(request.file, request.filters, dry_run=dry_run)

        return {"action": "unknown", "error": f"Unsupported action: {request.action}"}

    def _record(self, result: dict, action: str | None = None) -> dict:
        if action and "action" not in result:
            result["action"] = action
        self.history.append({"role": "assistant", "content": _short(result)})
        self.log.write("result", result=result)
        return result

    def _route_followup(self, msg: str, schemas: list[dict]) -> dict | None:
        """Use LLM + state to decide if this is explain/refine/new request."""
        if not self._last_request or not self._last_result:
            return None

        previous_request = _request_to_dict(self._last_request)
        previous_summary = _result_summary(self._last_result)
        route = self.llm.parse_followup(
            user_message=msg,
            previous_request=previous_request,
            previous_result_summary=previous_summary,
            schemas=schemas,
        )
        self.log.write("followup_route", route=route, user=msg)
        mode = route.get("mode")

        rewritten = route.get("rewritten_request")

        if mode == "explain_previous":
            return self._record(self._explain_last(), action="explain")

        if mode in {"refine_previous", "new_request"}:
            if not isinstance(rewritten, dict):
                # Router didn't include the rewritten request; fall back so the
                # caller can run parse_intent (one extra LLM call as a backup).
                return None
            req = ToolRequest.from_dict(rewritten)
            # Structural sanity: if the router produced something unusable
            # (no valid action/file, or filter conditions missing columns),
            # bail out and let the caller retry with a fresh parse_intent.
            problems = _validate_request_structure(req)
            if problems:
                self.log.write(
                    "followup_route_invalid",
                    problems=problems,
                    rewritten=rewritten,
                    user=msg,
                )
                return None
            # Refine mode never triggers writes; explicit new_request can.
            if mode == "refine_previous" and req.action in WRITE_ACTIONS:
                return self._record(
                    {
                        "action": "error",
                        "error": "Refine-followup cannot trigger write actions. Please ask explicitly.",
                    }
                )
            self.log.write("intent", user=msg, request=req.__dict__, source="followup_router")
            # Writes still go through dry-run + confirmation in the main flow,
            # so honour that here too instead of dispatching directly.
            if req.action in WRITE_ACTIONS and not req.confirm:
                self._pending_confirmation = req
                try:
                    preview = self._run_tool(req, dry_run=True)
                except (ValueError, ColumnResolutionError) as e:
                    self._pending_confirmation = None
                    return self._record({"action": "error", "error": str(e)})
                preview["__pending_action__"] = req.action
                preview["__followup__"] = "Reply 'yes' to commit, 'no' to cancel."
                return self._record(preview, action=req.action)
            return self._dispatch(req, requested_message=msg)

        if mode == "unclear":
            return self._record(
                {
                    "action": "clarify",
                    "message": (
                        "Do you want me to explain the previous result, refine it, "
                        "or run a brand new query?"
                    ),
                }
            )
        return None

    def _explain_last(self) -> dict:
        req = self._last_request
        if not req:
            return {"action": "explain", "reason": "No previous request available."}
        if req.action == "query":
            conditions = []
            for c in req.filters.conditions:
                if c.op in {"is_null", "is_not_null"}:
                    conditions.append(f"{c.column} {c.op.replace('_', ' ')}")
                elif c.op == "between" and isinstance(c.value, (list, tuple)) and len(c.value) == 2:
                    conditions.append(f"{c.column} between {c.value[0]} and {c.value[1]}")
                else:
                    conditions.append(f"{c.column} {c.op} {c.value}")
            return {
                "action": "explain",
                "file": req.file,
                "reason": "Rows were selected because they satisfy the previous query filters.",
                "filters_logic": req.filters.logic,
                "conditions": conditions or ["(no filters; all rows match)"],
                "sort": [f"{s.column} {s.order}" for s in req.sort] or ["(no sorting)"],
                "limit": req.limit,
                "matched_rows": self._last_result.get("matched_rows") if self._last_result else None,
                "returned_rows": self._last_result.get("returned_rows") if self._last_result else None,
            }
        if req.action == "aggregate" and req.aggregate:
            agg = req.aggregate
            return {
                "action": "explain",
                "file": req.file,
                "reason": "This result is an aggregation over matching rows.",
                "group_by": agg.group_by or ["(no grouping; global aggregate)"],
                "metrics": [
                    f"{m.alias or (m.agg + '_' + m.column)} = {m.agg}({m.column})"
                    for m in agg.metrics
                ],
                "derived": [f"{d.name} = {d.expr}" for d in agg.derived] or ["(none)"],
                "having": [
                    (
                        f"{c.column} {c.op} {c.value}"
                        if c.op not in {"is_null", "is_not_null"}
                        else f"{c.column} {c.op.replace('_', ' ')}"
                    )
                    for c in agg.having.conditions
                ] or ["(none)"],
                "matched_rows": self._last_result.get("matched_rows") if self._last_result else None,
                "returned_rows": self._last_result.get("returned_rows") if self._last_result else None,
            }
        return {
            "action": "explain",
            "reason": f"The last action was '{req.action}'. Ask a direct query to inspect rows.",
        }


def _validate_request_structure(req: ToolRequest) -> list[str]:
    """Catch obviously-broken requests from the follow-up router so we can
    fall back to a fresh intent parse instead of throwing a cryptic error."""
    problems: list[str] = []
    if req.action == "unknown" or req.file == "unknown":
        problems.append("router emitted unknown action or file")

    def _check_filter(label: str, fs) -> None:
        for i, c in enumerate(fs.conditions):
            if not c.column or not str(c.column).strip():
                problems.append(f"{label}.conditions[{i}] missing column name")

    _check_filter("filters", req.filters)
    if req.aggregate:
        _check_filter("aggregate.filters", req.aggregate.filters)
        _check_filter("aggregate.having", req.aggregate.having)
        for i, m in enumerate(req.aggregate.metrics):
            if not m.column or not str(m.column).strip():
                problems.append(f"aggregate.metrics[{i}] missing column")
    return problems


def _short(result: dict) -> str:
    if "error" in result:
        return f"error: {result['error']}"
    if "matched_rows" in result:
        return f"{result.get('action')}: matched={result['matched_rows']}"
    if result.get("action") == "clarify":
        return "clarify"
    return result.get("action") or "ok"


def _request_to_dict(req: ToolRequest) -> dict:
    out: dict[str, Any] = {
        "action": req.action,
        "file": req.file,
        "filters": {
            "logic": req.filters.logic,
            "conditions": [
                {"column": c.column, "op": c.op, "value": c.value}
                for c in req.filters.conditions
            ],
        },
        "sort": [{"column": s.column, "order": s.order} for s in req.sort],
        "columns": list(req.columns),
        "limit": req.limit,
        "data": dict(req.data),
        "updates": dict(req.updates),
        "confirm": req.confirm,
        "explanation": req.explanation,
    }
    if req.aggregate:
        out["aggregate"] = {
            "derived": [{"name": d.name, "expr": d.expr} for d in req.aggregate.derived],
            "group_by": list(req.aggregate.group_by),
            "metrics": [
                {"agg": m.agg, "column": m.column, "alias": m.alias}
                for m in req.aggregate.metrics
            ],
            "sort": [{"column": s.column, "order": s.order} for s in req.aggregate.sort],
            "limit": req.aggregate.limit,
            "filters": {
                "logic": req.aggregate.filters.logic,
                "conditions": [
                    {"column": c.column, "op": c.op, "value": c.value}
                    for c in req.aggregate.filters.conditions
                ],
            },
            "having": {
                "logic": req.aggregate.having.logic,
                "conditions": [
                    {"column": c.column, "op": c.op, "value": c.value}
                    for c in req.aggregate.having.conditions
                ],
            },
        }
    return out


def _result_summary(result: dict) -> dict:
    keys = [
        "action",
        "file",
        "matched_rows",
        "returned_rows",
        "columns",
        "group_by",
        "metrics",
        "limit",
        "reason",
    ]
    summary = {k: result.get(k) for k in keys if k in result}
    rows = result.get("rows") or []
    if isinstance(rows, list):
        summary["sample_rows"] = rows[:2]
    return summary
