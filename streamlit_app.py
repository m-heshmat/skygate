"""Streamlit interface for the Excel AI Assistant.

Run:
    streamlit run streamlit_app.py
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.assistant import ExcelAssistant


st.set_page_config(page_title="Excel AI Assistant", page_icon="📊", layout="wide")


def get_assistant() -> ExcelAssistant:
    if "assistant" not in st.session_state:
        st.session_state.assistant = ExcelAssistant()
    return st.session_state.assistant


def get_messages() -> list[dict]:
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": (
                    "Ask anything about real estate or marketing campaigns.\n\n"
                    "Examples:\n"
                    "- Show me 5 condos in Texas under 300000\n"
                    "- Average ROI by channel for campaigns in 2025\n"
                    "- Update campaign CMP-8001 budget to 30000"
                ),
            }
        ]
    return st.session_state.messages


def result_to_text(result: dict) -> str:
    if "error" in result:
        return f"Error: {result['error']}"
    if result.get("action") == "cancelled":
        return result.get("message", "Cancelled.")
    action = result.get("action", "result")
    if action in {"query", "aggregate"}:
        return (
            f"{result.get('matched_rows', 0)} rows matched, "
            f"showing {result.get('returned_rows', 0)}."
        )
    if action == "schema":
        return f"Schema for `{result.get('file')}` ({result.get('row_count', 0)} rows)."
    if action == "explain":
        return result.get("reason", "Explanation for the previous result.")
    if action == "clarify":
        return result.get("message", "Please clarify your request.")
    if action == "insert" and result.get("dry_run"):
        return "Dry run: this row will be inserted."
    if action == "insert":
        return f"Inserted row into `{result.get('file')}`."
    if action == "update" and result.get("dry_run"):
        return f"Dry run: would update {result.get('matched_rows', 0)} rows."
    if action == "update":
        return f"Updated {result.get('updated_rows', 0)} rows."
    if action == "delete" and result.get("dry_run"):
        return f"Dry run: would delete {result.get('matched_rows', 0)} rows."
    if action == "delete":
        return f"Deleted {result.get('deleted_rows', 0)} rows."
    return "Done."


def render_result(result: dict) -> None:
    text = result_to_text(result)
    st.markdown(text)

    if "rows" in result and result["rows"]:
        df = pd.DataFrame(result["rows"])
        st.dataframe(df, use_container_width=True)
    elif "columns" in result and isinstance(result.get("columns"), list):
        # schema action
        if result.get("columns") and isinstance(result["columns"][0], dict):
            st.dataframe(pd.DataFrame(result["columns"]), use_container_width=True)

    if result.get("preview"):
        st.caption("Preview")
        st.dataframe(pd.DataFrame(result["preview"]), use_container_width=True)

    if result.get("preview_before"):
        st.caption("Preview (before update)")
        st.dataframe(pd.DataFrame(result["preview_before"]), use_container_width=True)

    if result.get("would_insert"):
        st.caption("Row to insert")
        st.dataframe(pd.DataFrame([result["would_insert"]]), use_container_width=True)

    if result.get("inserted"):
        st.caption("Inserted row")
        st.dataframe(pd.DataFrame([result["inserted"]]), use_container_width=True)

    if result.get("updates"):
        st.caption("Updates")
        st.json(result["updates"], expanded=False)

    if result.get("action") == "explain":
        lines: list[str] = []
        if result.get("file"):
            lines.append(f"- **File:** `{result['file']}`")
        if result.get("filters_logic"):
            lines.append(f"- **Logic:** `{result['filters_logic']}`")
        if result.get("conditions"):
            lines.append("- **Conditions:**")
            lines.extend([f"  - {item}" for item in result["conditions"]])
        if result.get("sort"):
            lines.append("- **Sort:**")
            lines.extend([f"  - {item}" for item in result["sort"]])
        if result.get("group_by"):
            lines.append("- **Group by:**")
            lines.extend([f"  - {item}" for item in result["group_by"]])
        if result.get("metrics"):
            lines.append("- **Metrics:**")
            lines.extend([f"  - {item}" for item in result["metrics"]])
        if result.get("derived"):
            lines.append("- **Derived:**")
            lines.extend([f"  - {item}" for item in result["derived"]])
        if result.get("having"):
            lines.append("- **Having:**")
            lines.extend([f"  - {item}" for item in result["having"]])
        if result.get("limit") is not None:
            lines.append(f"- **Limit:** {result['limit']}")
        if result.get("matched_rows") is not None:
            lines.append(f"- **Matched rows:** {result['matched_rows']}")
        if result.get("returned_rows") is not None:
            lines.append(f"- **Returned rows:** {result['returned_rows']}")
        if lines:
            st.markdown("\n".join(lines))

    if result.get("__followup__"):
        st.warning(result["__followup__"])
        c1, c2 = st.columns([1, 1])
        if c1.button("Confirm", type="primary", key=f"confirm-{len(get_messages())}"):
            assistant = get_assistant()
            confirmed = assistant.handle_message("yes")
            get_messages().append({"role": "assistant", "result": confirmed})
            st.rerun()
        if c2.button("Cancel", key=f"cancel-{len(get_messages())}"):
            assistant = get_assistant()
            cancelled = assistant.handle_message("no")
            get_messages().append({"role": "assistant", "result": cancelled})
            st.rerun()


def _submit_prompt(prompt: str) -> None:
    assistant = get_assistant()
    messages = get_messages()
    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="👤"):
        st.markdown(prompt)
    with st.chat_message("assistant", avatar="🧠"):
        with st.spinner("Thinking..."):
            result = assistant.handle_message(prompt)
            render_result(result)
    messages.append({"role": "assistant", "result": result})


def main() -> None:
    assistant = get_assistant()
    messages = get_messages()

    st.title("📊 Excel AI Assistant")
    st.caption("Custom Python tools + Gemini intent parsing")

    with st.sidebar:
        st.header("Controls")
        if st.button("Reset working data", use_container_width=True):
            assistant.reset_data()
            st.success("Working copies restored from originals.")
        if st.button("Clear chat", use_container_width=True):
            st.session_state.pop("messages", None)
            st.rerun()
        st.markdown("---")
        st.markdown("**Tips**")
        st.markdown("- Ask for filters, sorting, and top N.")
        st.markdown("- Writes run as dry-run first, then confirm.")
        st.markdown("- Use `Schema of marketing` for columns.")

    for i, msg in enumerate(messages):
        with st.chat_message(msg["role"], avatar="🧠" if msg["role"] == "assistant" else "👤"):
            if "content" in msg:
                st.markdown(msg["content"])
            if "result" in msg:
                render_result(msg["result"])

    if st.session_state.get("_quick_prompt"):
        prompt = st.session_state.pop("_quick_prompt")
        _submit_prompt(prompt)
        st.stop()

    if prompt := st.chat_input("Ask about real_estate or marketing..."):
        _submit_prompt(prompt)


if __name__ == "__main__":
    main()
