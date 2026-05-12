# Decisions

Every non-obvious choice I made during this project, why I made it, and what I rejected.

---

## 1. The LLM is an intent parser, not an executor

The LLM makes one call per user turn. Its only job is to translate the user's message into a typed `ToolRequest` JSON object. Python code does the actual data work.

I rejected a ReAct loop (multi-step tool calling) because it adds 3-5x token cost, introduces "the model got stuck calling the same tool" failure modes, and is unnecessary when schema-aware prompting gets the intent right in one shot for this scope of questions.

I also rejected Gemini's function-calling API. Function calling is essentially the same JSON-intent pattern with a different transport. I wanted the contract to be a plain Python dataclass I own, not something tied to Google's SDK.

The trade-off: if the LLM emits the wrong intent, the user has to rephrase. There's no self-correction loop. In practice, with the schema injected into the prompt, this rarely happens for reasonable questions about these files.

## 2. Follow-ups use conversation history, not a routing layer

I initially built a separate follow-up router that classified every message into one of four modes (explain, refine, new request, unclear) before acting on it. That turned out to be the single biggest source of bugs in the system:

- It blocked write actions that came from a "refine" classification, even when the user was explicitly asking for an update.
- It sometimes emitted structurally invalid requests (filters with no column name).
- It misclassified "show me the table" after a write as "explain previous" instead of a new query.

I replaced it with a simpler approach: the intent parser always receives the conversation history and the previous request/result as structured context. The LLM naturally handles follow-ups because it can see what happened before. One code path instead of four branches.

I rejected using LangChain or LangGraph's memory features because the task explicitly forbade agent frameworks. But the underlying concept is the same — maintain a conversation buffer and inject it as context. I just do it without the dependency.

The conversation history entries are compact but informative (file, action, matched rows, column names, first-row sample). This gives the LLM enough context to resolve "only sold ones" or "update the second row" without a separate classification step.

## 3. Every tool is a plain class, no framework

A Tool is a Python class with `__init__(self, store)` and `run(...)`. The assistant is a ~100-line dispatcher. No base class, no registry, no plugin system.

I rejected building a generic Tool base class with name/schema/validate/run methods. For six tools that's overengineering. If the project grew to 15+ tools I'd refactor.

## 4. Operator-aware filters, not equality/substring matching

Filters are a list of `Condition(column, op, value)` with an and/or logic flag and a real operator vocabulary: `=, !=, >, >=, <, <=, between, in, not_in, contains, starts_with, ends_with, is_null, is_not_null`.

I rejected the simpler `{column: value}` dict approach where strings do substring matching and everything else does `==`. That approach is wrong for these datasets — 8 of the 22 columns are numeric and 4 are dates. "Properties under 300k" and "campaigns in Q3 2025" are the default questions, and they need real operators.

On top of this, I added operator alias normalization at the schema boundary. The LLM sometimes emits SQL-style names like `gt`, `eq`, `ne` instead of `>`, `=`, `!=`. Rather than relying purely on prompt engineering, I added an `OP_ALIASES` map in `schemas.py` that normalizes these before validation. Two layers of defense: the prompt steers the model toward the right token, and the normalizer catches it when the model drifts.

## 5. Aggregations are first-class, with HAVING support

An `aggregate` action supports derived columns (via `df.eval()`), group_by, metrics (sum/mean/median/min/max/count/nunique/std), sort, and limit.

I rejected treating aggregation as a special-case query. The most natural questions about marketing data are aggregations — best ROI, total revenue, average CTR. Without first-class support, the assistant can't answer half of them.

Derived columns use `pandas.DataFrame.eval()` because it parses a small expression grammar (arithmetic + column refs in backticks) and refuses anything else. It can't import modules, call functions, or read files. The alternative — a custom AST evaluator — was unnecessary complexity for this scope.

I also added a distinction between pre-aggregate filters (`filters` — applied to source rows before grouping) and post-aggregate HAVING (`having` — applied to grouped results on metric aliases). Without this, "only channels with avg ROI above 2" would fail because `avg_roi` doesn't exist on the raw data. The two filter stages use the same `FilterSpec` shape but operate at different points in the pipeline.

Similarly to operators, I added aggregation alias normalization. The LLM sometimes emits `avg` instead of `mean` or `total` instead of `sum`. The `AGG_ALIASES` map in `schemas.py` handles this transparently.

## 6. Schema-aware prompting

Every LLM call includes the full schemas for both files — column names, dtypes, unique counts, null counts, and sample values per column. The model never has to guess what `Channel` ranges over or whether `List Price` is an int.

I rejected a static prompt with hand-written column lists. If the file changes, the prompt drifts. Pulling the schema from the actual loaded DataFrame keeps prompt and data in sync.

Cost: roughly 600 extra prompt tokens per call. Without this, accuracy on column names drops significantly because the columns have spaces and Title Case (`List Price`, not `list_price`).

## 7. Fuzzy column resolution with loud failure

`ColumnResolver` matches in three passes — exact, normalized (lowercase, strip non-alphanumerics), then `difflib` close-match at 0.85 cutoff. If nothing matches, it raises `ColumnResolutionError` with the available columns and a suggestion.

I rejected the original behavior of `if column not in df.columns: continue`. That silently drops the filter, the query matches everything, and the user gets a confidently wrong answer. For a `delete` action, silent filter failure means wiping the entire file.

## 8. Three-layer write safety

1. The originals in `data/` are read-only inputs. The store copies them into `data/working/` on first access and only writes to the copies. Reset restores from originals.
2. `update` and `delete` refuse to run with an empty filter spec.
3. Every write returns a dry-run preview first. The user must confirm before the mutation happens. The Streamlit UI renders this as Confirm/Cancel buttons.
4. Saves are atomic — write to a temp file, then `os.replace()`. A crash mid-save can't leave a half-written xlsx.

I rejected overwriting the source files in place. One bad LLM-emitted delete could destroy the assignment data.

## 9. Typed dataclasses as the LLM/Python boundary

`ToolRequest.from_dict()` validates everything: action and file are clamped to allowed enums, limit is forced to a positive int and capped at 200, malformed nested dicts collapse to safe defaults, operator and aggregation aliases are normalized before validation.

I rejected trusting `parsed.get(...)` directly. The original draft had a bug where `parsed.get("limit", 10)` returns `None` (not 10) when the LLM emits `"limit": null`. Then `df.head(None)` returns the entire DataFrame. Centralizing validation in one place eliminates this whole class of bug.

I rejected Pydantic. It would work fine here, but pulling in a dependency for what is two pages of dataclass code is overkill, and I wanted the contract to be obvious from reading one file.

## 10. Gemini 2.5 Flash-Lite via google-genai, JSON mode

Model: `gemini-2.5-flash-lite` with `response_mime_type="application/json"` and `temperature=0.1`.

I rejected the deprecated `google-generativeai` package — it's frozen and one of its dependencies broke at install time. I also rejected `gemini-1.5-flash` (retired, returns 404) and higher temperatures (for intent parsing, determinism beats creativity).

I chose `gemini-2.5-flash-lite` over `gemini-2.5-flash` because of free-tier quotas. Flash-Lite gives the same JSON-mode support with a more generous daily limit. The quality difference is negligible for structured intent parsing.

Forcing `response_mime_type="application/json"` eliminates the markdown-fencing problem — without it, Gemini wraps responses in code fences about 10% of the time.

The client also includes a 429 retry mechanism that parses the server's `retryDelay` hint and waits accordingly before retrying once. This handles transient quota exhaustion gracefully instead of surfacing it as an error to the user.

## 11. Streamlit UI on top of the same orchestrator

The repo includes both a CLI (`main.py`) and a Streamlit chat UI (`streamlit_app.py`). Both are thin shells calling the same `ExcelAssistant.handle_message()` method.

I kept both because having two frontends proves the system isn't UI-coupled. The orchestrator, tools, and safety model don't know or care which interface is calling them. The CLI is useful for scripted testing and one-shot runs; Streamlit is better for interactive use and demos.

## 12. Per-session JSONL log

Every parsed intent and every tool result is appended to `logs/session-<timestamp>.jsonl`. Cheap, append-only, grep-friendly. When someone asks "what did the LLM actually emit for that question?", I can show them the exact JSON.

## 13. Tests cover the tool layer without the LLM

18 tests in `tests/test_tools.py` exercise the tool layer end-to-end against the real Excel files. They cover operator filters, fuzzy column resolution, the delete-with-unknown-column regression, dry-run no-op, atomic save round-trip, ID auto-generation, duplicate-ID rejection, derived aggregations, and HAVING-style post-aggregate filtering on metric aliases.

I rejected mocking Gemini in tests. The tool layer is the part that must be correct; the LLM is replaceable. Keeping them decoupled means I can verify correctness without burning API quota or dealing with flaky external calls.

---

# Known limits

- **No self-correcting retry.** If the LLM emits a wrong column or invalid op, the user has to rephrase. A retry that feeds the validation error back to the LLM would fix most of these in one extra round trip.
- **No cross-file joins.** The two files share no key, so this isn't an actual use case for the provided data.
- **Aggregate `matched_rows` means number of groups, not source rows.** This is a known labeling ambiguity in the output. The data is correct; the label is potentially misleading.
- **Numeric precision.** Money columns mix ints and floats. For ROI/CTR I let pandas do float arithmetic; production code should use Decimal for financial roll-ups.
- **`update` can't use expressions as values.** You can set a column to a literal, but not to "current value * 1.10".
- **No row-level concurrency control.** Two parallel processes would race on the working files. Atomic saves prevent half-written files but not lost updates.
- **The LLM occasionally picks the wrong file** for ambiguous words ("show me revenue" — campaigns or sales?). Schema-aware prompting helps but doesn't eliminate this. A clarifying-question fallback is the right fix.

---

# What I'd change with more time

1. **Self-correcting LLM retry** — feed validation errors back into a second call. Most wrong-column mistakes self-heal in one extra round trip.
2. **A `compare` action** — "how does California compare to Texas?" needs a dedicated tool to be clean.
3. **Derived-expression updates** — `update budget = budget * 1.1 where channel = 'Email'`.
4. **Token-budget middleware** — count prompt tokens before sending and prune context if needed, instead of relying on retry after hitting the limit.
5. **Replace Excel with DuckDB for the working layer** — xlsx as the wire format, columnar storage for the working representation.
