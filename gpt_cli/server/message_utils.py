import json
from typing import Any


def _extract_content(content_obj: Any) -> str:
    if isinstance(content_obj, str):
        return content_obj
    if isinstance(content_obj, list):
        parts: list[str] = []
        for item in content_obj:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                c_type = item.get("type")
                if c_type == "text":
                    parts.append(item.get("text", ""))
                elif c_type == "tool_result":
                    sub_c = item.get("content", "")
                    sub_text = _extract_content(sub_c)
                    tool_use_id = item.get("tool_use_id") or item.get("id") or ""
                    id_suffix = f" (call_id: {tool_use_id})" if tool_use_id else ""
                    prefix = "[Tool Error]" if item.get("is_error") else "[Tool Output]"
                    parts.append(f"{prefix}{id_suffix}: {sub_text}")
                elif c_type == "tool_use":
                    t_name = item.get("name", "tool")
                    t_input = item.get("input", {})
                    t_id = item.get("id") or ""
                    id_suffix = f" call_id={t_id}" if t_id else ""
                    parts.append(f"[Tool Call: {t_name}{id_suffix} input={json.dumps(t_input)}]")
                elif c_type == "thinking":
                    th_text = item.get("thinking") or item.get("text") or ""
                    if th_text:
                        parts.append(f"[Thinking]: {th_text}")
        return "\n".join([p for p in parts if p.strip()])
    return str(content_obj or "")
