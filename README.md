# Junior AI Excel Assistant

A natural-language assistant that can read, query, aggregate, insert, update, and delete data across two Excel files using a custom Python tool layer. **No agent frameworks** (no LangChain, LlamaIndex, AutoGen, CrewAI), free LLM (Gemini), Python only.

## Files it understands

| Key | File | Description |
|---|---|---|
| `real_estate` | `data/Real Estate Listings.xlsx` | 1,000 U.S. property listings (Listing ID, Property Type, City, State, Bedrooms, Bathrooms, Square Footage, Year Built, List Price, Sale Price, Listing Status). |
| `marketing` | `data/Marketing Campaigns.xlsx` | 1,000 campaign rows (Campaign ID, Campaign Name, Channel, Start Date, End Date, Budget Allocated, Amount Spent, Impressions, Clicks, Conversions, Revenue Generated). |

The originals are treated as **read-only inputs**. The assistant works on copies under `data/working/` so a bad write never destroys your source data. `/reset` (or `ExcelStore.reset()`) restores from the originals.

## Quick start

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
# edit .env and set GEMINI_API_KEY

python main.py                    # interactive REPL
python main.py "your question"    # one-shot
python main.py --auto-confirm "delete listings in Wyoming"
streamlit run streamlit_app.py    # web UI
```

Get a free Gemini key at <https://aistudio.google.com/apikey>.

## Streamlit interface

The repo includes a chat-like web UI in `streamlit_app.py`.

### Features

- Chat UI with persistent session state.
- DataFrame results rendered as interactive tables.
- Dry-run previews for write operations with **Confirm** / **Cancel** buttons.
- Sidebar controls for **Reset working data** and **Clear chat**.

### Run

```powershell
venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## What it can do (real examples)

```text
> Show me 5 condos in Texas under 300000
18 rows matched, showing 5.
| Listing ID | Property Type | City        | State | Bedrooms | ... | List Price |
| LST-5362   | Condo         | San Antonio | Texas |        1 | ... |     48,000 |
...

> Average ROI by channel for campaigns in 2025
5 rows matched, showing 5.
| Channel    | avg_roi |
| Email      |  12.36  |
| LinkedIn   |   5.46  |
| Google Ads |   4.88  |
| Facebook   |   3.72  |
| Instagram  |   3.35  |

> Top 10 most expensive listings
1000 rows matched, showing 10.

> Schema of marketing
File: marketing (1000 rows)
| column            | dtype          | n_unique | n_null | samples |
| Channel           | str            |        5 |      0 | Facebook, LinkedIn, ... |
...
```

The assistant supports six actions:

| Action | What it does |
|---|---|
| `schema` | Lists columns, dtypes, uniques, nulls, sample values for a file. |
| `query` | Filter / sort / project / limit and return rows. |
| `aggregate` | `group_by` + aggregations (`sum`/`mean`/`median`/`min`/`max`/`count`/`nunique`/`std`) over base or derived columns. |
| `insert` | Add a new row. Auto-generates IDs in the existing pattern (`LST-####`, `CMP-####`) if not supplied. |
| `update` | Mutate matching rows. **Refuses** to run with no filter. |
| `delete` | Remove matching rows. **Refuses** to run with no filter. |

Filters use a real operator vocabulary, not just substring/equality:

```
=, !=, >, >=, <, <=, between, in, not_in,
contains, starts_with, ends_with, is_null, is_not_null
```

Aggregations support **derived columns** via safe `df.eval()` expressions, e.g.:

```text
roi = `Revenue Generated` / `Amount Spent`
ctr = `Clicks` / `Impressions`
sale_to_list = `Sale Price` / `List Price`
```

Aggregations also distinguish **pre-aggregate filters** (`filters` — applied to source rows) from **post-aggregate HAVING** (`having` — applied to grouped rows on metric aliases). So *"only channels with avg_roi > 1"* is a HAVING on the alias, not a filter that would fail because `avg_roi` doesn't exist on the raw data.

## Multi-turn conversations

The assistant remembers the last request and result, so follow-ups work without re-stating context:

```text
> Top 10 campaigns by ROI
479 rows matched, showing 10.
...

> only Facebook and Instagram
176 rows matched, showing 10.
... (same aggregate, restricted to those two channels)

> why were these selected?
Explanation: ...
```

A single LLM call per turn handles all three cases. A small **state-aware follow-up router** (running inside the same call as intent parsing) decides whether the message should:

- `explain_previous` — explain the prior filters/groups, no tool call,
- `refine_previous` — tweak the prior request (e.g. add a filter, change the limit),
- `new_request` — start over with a fresh request,
- `unclear` — ask a clarifying question.

If the router produces a structurally bad request (e.g. a filter without a column), the assistant logs the issue and transparently falls back to a fresh intent parse instead of erroring.

## Architecture

```
User input
    │
    ▼
ExcelAssistant (orchestrator)
    │   1. Pulls schemas (cols + dtypes + sample values) for both files
    │   2. Calls LLM with schema-aware prompt → strict JSON
    │   3. Validates JSON into a typed ToolRequest
    │   4. Dispatches to the right tool
    │
    ├──► SchemaTool        →  describe a file
    ├──► QueryTool         →  filter / sort / project / limit
    ├──► AggregateTool     →  group_by / agg / derived columns
    ├──► InsertTool        →  add row, coerce dtypes, auto-ID
    ├──► UpdateTool        →  mutate matching rows (filter required)
    └──► DeleteTool        →  remove matching rows (filter required)
                │
                ▼
       FilterEngine + ColumnResolver  (operator-aware, fuzzy column names)
                │
                ▼
       ExcelStore (atomic save, working-copy, in-memory cache)
```

Project layout:

```
.
├── main.py                       # CLI (REPL + one-shot)
├── streamlit_app.py              # Streamlit chat UI
├── requirements.txt
├── .env.example
├── README.md
├── DECISIONS.md
├── data/
│   ├── Real Estate Listings.xlsx     # source (read-only)
│   ├── Marketing Campaigns.xlsx      # source (read-only)
│   └── working/                      # mutated copies live here
├── app/
│   ├── config.py                 # paths, model name, API key
│   ├── schemas.py                # ToolRequest, FilterSpec, AggregateSpec, ...
│   ├── assistant.py              # orchestrator + multi-turn confirmation
│   ├── llm/
│   │   ├── client.py             # google-genai client, JSON-mode response
│   │   └── prompts.py            # schema-aware system prompt + examples
│   ├── tools/
│   │   ├── excel_store.py        # atomic save, working-copy, cache
│   │   ├── column_resolver.py    # fuzzy column name resolution
│   │   ├── filter_engine.py      # operator-aware boolean masks
│   │   ├── schema_tool.py
│   │   ├── query_tool.py
│   │   ├── aggregate_tool.py
│   │   ├── insert_tool.py
│   │   ├── update_tool.py
│   │   └── delete_tool.py
│   └── utils/
│       ├── formatters.py         # tabulate-based result rendering
│       └── logging_setup.py      # JSONL session log under logs/
├── tests/
│   └── test_tools.py             # 17 offline tool-layer tests
└── logs/                         # per-session JSONL transcripts
```

## Safety model

Two-layer protection against the LLM doing something destructive:

1. **Static guards in the tools** — `update`/`delete` raise immediately if the filter spec is empty; unknown columns raise `ColumnResolutionError` instead of being silently dropped (the most common cause of "wiped the whole file" bugs); `update`/`insert` coerce values to the target column dtype and surface a clear error on mismatch.
2. **Dry-run + confirmation in the orchestrator** — every `insert`/`update`/`delete` first returns a preview of what *would* change. Reply `yes` to commit, `no` to cancel. Pass `--auto-confirm` to skip this for scripted runs.

Plus:

- The originals in `data/` are never written to. All mutations go to `data/working/`.
- Saves are atomic: write to a temp file in the same directory, then `os.replace()`.
- Every LLM call and every tool result is appended to a per-session JSONL log under `logs/`.
- LLM-emitted operator and aggregation aliases are normalized at the schema boundary (`gt` → `>`, `avg` → `mean`, `total` → `sum`, etc.) so prompt creativity doesn't break validation.
- 429 quota-exhausted responses from Gemini are caught and retried once, respecting the server's `retryDelay` hint.

## Tests

```powershell
pip install pytest
python -m pytest tests/test_tools.py -q
```

The 18 tests cover the tool layer end-to-end against the real Excel files (no LLM required): operator filters, fuzzy column resolution, the `mask=True` regression for delete, dry-run no-op, atomic save round-trip, ID auto-generation, duplicate-ID rejection, ROI/CTR-style derived aggregations, and HAVING-style post-aggregate filtering on metric aliases.

## Configuration

`.env`:

```env
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash-lite
```

The default is `gemini-2.5-flash-lite` because it has the most generous free-tier daily quota for this workload. Any chat-capable Gemini model that supports `response_mime_type=application/json` will work — `gemini-2.5-flash` is smarter but rate-limited to ~20 requests/day on the free tier; `gemini-2.0-flash-lite` is a good alternate quota bucket if you hit 429s.

## Known limits

See `DECISIONS.md` for the full list. Highlights:

- One LLM call per turn (no ReAct loop). The follow-up router shares that single call; if it produces a structurally invalid request, the assistant falls back to a fresh intent parse, which costs one extra call on that turn.
- Cross-file joins aren't supported (the two files share no key, so this isn't currently a real use case).
- The `update`/`insert` payloads come from the LLM, so dtype coercion errors will sometimes surface to the user instead of being self-healed.
- Safe expression engine for derived columns is `pandas.DataFrame.eval()` — arithmetic on column references only, no Python.
- For aggregate results, the displayed `matched_rows` is the number of *groups* after group-by, not the number of source rows. This is documented in `DECISIONS.md`.
