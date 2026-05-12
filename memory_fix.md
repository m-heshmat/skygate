# Memory Issue Fix Summary

I fixed the memory issue by changing four files, and each one had a clear role.

## 1. `schemas.py`

I added `explain` as a proper action type.

Before, the system only recognized actions like:

- `schema`
- `query`
- `aggregate`
- `insert`
- `update`
- `delete`
- `unknown`

So when the user asked something like “explain the previous result,” it was handled by a separate routing layer instead of being treated as a normal action.

Now the LLM can return:

```json
{
  "action": "explain"
}
```

just like it can return `action: query`, and the system handles it based on the stored previous request.

## 2. `prompts.py`

This was the biggest change.

I removed the separate follow-up router prompt completely. That prompt had its own modes, rules, and examples, which made the flow more complicated.

I replaced it with one unified system prompt that tells the LLM to use the conversation history.

I also added follow-up examples directly into the main prompt, such as:

- After showing condos in Texas, the user says “only sold ones” → emit a query with the extra filter.
- After an update, the user says “show me the table” → emit a query on the same file.

I also updated `build_intent_prompt` so it can include the previous request and result summary as structured context. This gives the LLM a clear view of what happened in the last turn.

## 3. `client.py`

I removed `parse_followup`.

Previously, there were two methods:

- `parse_intent` for new messages
- `parse_followup` for follow-ups

Now everything goes through `parse_intent`.

It can optionally receive conversation history and previous context, but it always returns the same response format: a `ToolRequest`.

So the flow is simpler:

```text
User message + optional history/context
        ↓
parse_intent
        ↓
ToolRequest
```

## 4. `assistant.py`

This is where the old follow-up router lived.

I removed `_route_followup`, which had a lot of branching logic:

- If mode is explain, do this.
- If mode is refine, do that.
- If the rewrite is invalid, fall back.
- If it is a write action, block it.

All of that is gone now.

`handle_message` simply calls `parse_intent` with the available history and context, gets back a `ToolRequest`, and dispatches it.

So whether it is the first message or the twentieth, the system follows the same code path.

I also improved the stored conversation history. Instead of saving something vague like:

```text
query: matched=18
```

each turn now stores more useful details, such as:

- file name
- columns
- sample rows
- key result details

This gives the LLM enough real context to handle follow-up requests properly.

## Result

The follow-up flow changed from:

```text
Two LLM calls + separate four-mode router
```

to:

```text
One LLM call + conversation history
```

The bugs I was seeing disappeared, including:

- writes being blocked incorrectly
- conditions missing column names
- post-write follow-ups going to the wrong branch

Those issues were caused by the extra classification layer, and now that layer no longer exists.
