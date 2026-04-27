# Skygate — Tool-Driven AI Assistant over Excel

A natural-language assistant that **reads, queries, inserts, modifies and deletes** rows
from two Excel datasets using a **custom tool layer built from scratch in Python** —
no LangChain, LlamaIndex, AutoGen, CrewAI or any other agent framework.

> Submission for the *Junior AI Engineer* task.
> Datasets:
> - `data/real_estate_listings.xlsx` — U.S. property listings
> - `data/marketing_campaigns.xlsx` — marketing campaign performance

---

## Quick start

```bat
git clone <your-fork-url> skygate
cd skygate

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
:: edit .env and put your free GROQ_API_KEY in it

pytest
python main.py
```

You'll get a REPL like this:

```
┌── Skygate AI Assistant ──┐
│ model      = llama-3.3-70b-versatile
│ listings   = ...real_estate_listings.xlsx
│ campaigns  = ...marketing_campaigns.xlsx
│ tools      = 14
└──────────────────────────┘
you: average list price by state for active condos
```

CLI commands inside the REPL:

| command   | what it does                       |
|-----------|------------------------------------|
| `/tools`  | list available tools                |
| `/verbose`| toggle showing each tool call       |
| `/reset`  | start a new conversation            |
| `/help`   | show help                           |
| `/exit`   | quit                                |

---

## What the assistant can do

### Real estate listings
- *"Show me the 10 cheapest houses in Texas."*
- *"How many active listings do we have in California?"*
- *"What's the average sale price per state?"*
- *"Add a new listing LST-9001, Townhouse in Boise, 3 bed / 2 bath, 1800 sqft, built 2010, list price 425000, status Active."*
- *"Mark LST-5012 as Sold and set sale price to 720000."*
- *"Delete listing LST-5099."*

### Marketing campaigns
- *"Top 5 campaigns by ROAS."*
- *"Total spend and revenue by channel."*
- *"What's the CPA for CMP-8042?"*
- *"Bump the budget of CMP-8001 to 30000."*
- *"Delete campaign CMP-8050."*

### Schema introspection
- *"What fields can I filter on for campaigns?"* → calls `describe_dataset`.

---

## Architecture (Clean / Hexagonal)

```
┌─────────────────────────── presentation/cli ───────────────────────────┐
│   REPL, formatters                                                     │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │  depends on
┌──────────────────────────────▼──────────────── application ────────────┐
│  agent/orchestrator   (tool-calling loop, no frameworks)               │
│  tools/               (Tool, ToolRegistry, JSON schemas)               │
│  use_cases/           (QueryListings, CreateCampaign, …)               │
│  ports/llm.py         (LLMClient interface)                            │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │  depends on (interfaces only)
┌──────────────────────────────▼─────────────────── domain ──────────────┐
│  entities (Listing, Campaign)   value_objects (QuerySpec, …)           │
│  repositories (ports)           exceptions                             │
└──────────────────────────────▲─────────────────────────────────────────┘
                               │  implements
┌──────────────────────────── infrastructure ────────────────────────────┐
│  persistence/   ExcelWorkbook, ExcelListingRepository, query_engine    │
│  llm/           GroqClient (raw httpx)                                 │
│  config.py      Settings.load()                                        │
└────────────────────────────────────────────────────────────────────────┘
```

**Dependency rule:** arrows point inward only. The domain depends on
nothing; infrastructure depends on the domain. Swapping Excel for SQLite
(or Groq for Gemini) means adding one adapter — no business code changes.

The full repo layout:

```
skygate/
├── main.py
├── data/                                # Excel files live here
├── src/skygate/
│   ├── domain/
│   │   ├── entities/        (Listing, Campaign — Pydantic models)
│   │   ├── repositories/    (ListingRepository, CampaignRepository ports)
│   │   ├── value_objects.py (QuerySpec, AggregateSpec, FilterCondition…)
│   │   └── exceptions.py
│   ├── application/
│   │   ├── ports/llm.py     (LLMClient interface)
│   │   ├── use_cases/       (one class per business operation)
│   │   ├── tools/           (Tool, ToolRegistry, JSON schemas)
│   │   ├── agent/           (orchestrator + system prompt)
│   ├── infrastructure/
│   │   ├── persistence/     (Excel adapters + shared query engine)
│   │   ├── llm/             (GroqClient via raw httpx)
│   │   └── config.py
│   ├── presentation/cli/    (REPL, formatters)
│   └── composition_root.py  (single place where DI is wired)
└── tests/                   (24 unit tests, no network required)
```

---

## How a turn works

1. CLI calls `AgentOrchestrator.ask(user_input, state)`.
2. Orchestrator sends `[system, *history]` + tool schemas to `LLMClient`.
3. If the model returns `tool_calls`, the registry dispatches each one
   to a `Tool.handler`, which delegates to a use case, which uses a
   repository through its **port** — never the Excel file directly.
4. Tool results are appended as `role="tool"` messages and the loop
   continues until the model returns a plain text reply (or `max_steps=8`).

---

## Tools exposed to the model (14 total)

| Domain    | Read                                                      | Write                                            |
|-----------|-----------------------------------------------------------|--------------------------------------------------|
| Listings  | `list_listings`, `get_listing`, `aggregate_listings`      | `create_listing`, `update_listing`, `delete_listing` |
| Campaigns | `list_campaigns`, `get_campaign`, `aggregate_campaigns`, `campaign_kpis` | `create_campaign`, `update_campaign`, `delete_campaign` |
| Meta      | `describe_dataset`                                        | —                                                |

Every tool input is a JSON Schema, so the model is constrained to
well-formed arguments. Filters share one schema:

```json
{ "field": "list_price", "op": "gte", "value": 500000 }
```

Operators: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `in`, `contains`.

---

## Safety / data integrity

- **Atomic writes** — every save goes to a sibling temp `.xlsx`, then `os.replace`.
  A killed process can't leave a half-written file.
- **In-process lock** on each `ExcelWorkbook` to serialise read/write.
- **Pydantic validation** on every entity, on both create and update paths.
- **Tool sandbox** — handlers can never crash the loop; exceptions become
  `{ok: false, error: "..."}` payloads the model is told to surface.
- **Destructive ops are flagged** (`Tool.confirms_write=True`) and the
  system prompt instructs the model to confirm ambiguous intents.

---

## Tests

```bat
pytest
```

The suite (24 tests, ~7 s) covers:

- domain validation rules and KPI math
- the pandas-backed query engine (filter / sort / aggregate)
- both Excel repositories on a tmp copy of the real dataset
- the tool registry (success, exception capture, unknown tool)
- the agent loop with a **scripted fake LLM** (no network)

---

## Configuration

Copy `.env.example` to `.env` and fill in:

```ini
GROQ_API_KEY=...
GROQ_URL=https://api.groq.com/openai/v1
GROQ_MODEL=llama-3.3-70b-versatile
LISTINGS_XLSX=data/real_estate_listings.xlsx
CAMPAIGNS_XLSX=data/marketing_campaigns.xlsx
```

Any OpenAI-compatible provider (Groq, OpenRouter, NVIDIA Build, …) works
by changing `GROQ_URL` and `GROQ_MODEL` — only the URL/model differ.

---

## License

MIT.
