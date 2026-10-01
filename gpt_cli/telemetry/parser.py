import codecs
import json
import re
from typing import Any
from .events import TelemetryEvent

COMPLETION_STATUSES = frozenset({"finished_successfully", "finished", "complete"})
V1_PARTS_PATH = "/message/content/parts/0"
V1_STATUS_PATH = "/message/status"


def _extract_exit_code(output: object, metadata: dict[str, Any]) -> int | None:
    """Normalize an exit status from structured metadata or standard text."""
    for source in (metadata, output if isinstance(output, dict) else {}):
        for key in ("exit_code", "returncode", "status_code"):
            value = source.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    if not isinstance(output, str):
        return None
    match = re.search(r"\b(?:exit(?:[ _-]?code)?|returncode)\s*[:=]\s*(-?\d+)\b", output, re.IGNORECASE)
    return int(match.group(1)) if match else None

class SSETelemetryParser:
    """Incremental, stream-safe SSE Telemetry and Text Delta Parser."""

    def __init__(self):
        self._buffer = ""
        self._pending_cr = False
        self._utf8 = codecs.getincrementaldecoder("utf-8")()
        self._accumulated_text = ""
        self._last_conv_id: str | None = None
        self._last_msg_id: str | None = None
        self._current_role: str | None = None
        self._current_recipient: str | None = None
        self._current_author_name: str | None = None
        self._current_content_type: str | None = None
        self._active_tool_calls: dict[str, str] = {}

    @staticmethod
    def _reference_id(message: dict[str, Any], metadata: dict[str, Any]) -> str | None:
        """Read a protocol-provided tool correlation id without naming a provider."""
        invoked = metadata.get("invoked_plugin") or metadata.get("invoked_resource") or {}
        candidates = (
            metadata.get("tool_call_id"),
            metadata.get("tool_use_id"),
            message.get("tool_call_id"),
            message.get("tool_use_id"),
            message.get("parent"),
            message.get("parent_message_id"),
            invoked.get("tool_call_id") if isinstance(invoked, dict) else None,
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate:
                return candidate
            if isinstance(candidate, dict):
                nested = candidate.get("id")
                if isinstance(nested, str) and nested:
                    return nested
        return None

    def _finish_tool_call(self, message: dict[str, Any], metadata: dict[str, Any]) -> tuple[str | None, str]:
        reference = self._reference_id(message, metadata)
        if reference in self._active_tool_calls:
            return reference, self._active_tool_calls.pop(reference)
        if reference is None and len(self._active_tool_calls) == 1:
            call_id, tool_name = self._active_tool_calls.popitem()
            return call_id, tool_name
        return reference, ""

    def feed(self, chunk: bytes | str) -> list[TelemetryEvent]:
        if isinstance(chunk, bytes):
            text = self._utf8.decode(chunk, final=False)
        else:
            text = chunk

        if not text and not self._pending_cr:
            return []

        # Handle CRLF across chunk boundaries
        if self._pending_cr:
            if text.startswith("\n"):
                self._buffer += "\n"
                text = text[1:]
            else:
                self._buffer += "\n"
            self._pending_cr = False

        if text.endswith("\r"):
            self._pending_cr = True
            text = text[:-1]

        self._buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        events: list[TelemetryEvent] = []

        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            lines = frame.split("\n")
            data_lines: list[str] = []
            for line in lines:
                line_str = line.strip()
                if line_str.startswith("data:"):
                    # WHATWG SSE: remove data: prefix and at most one leading space
                    content_after_prefix = line[line.find("data:") + 5 :]
                    if content_after_prefix.startswith(" "):
                        content_after_prefix = content_after_prefix[1:]
                    data_lines.append(content_after_prefix)

            if not data_lines:
                continue

            data_str = "\n".join(data_lines)
            if not data_str:
                continue

            if data_str == "[DONE]":
                events.append(TelemetryEvent(event_type="done", data={}))
                continue

            try:
                payload = json.loads(data_str)
            except Exception:
                continue

            if not isinstance(payload, dict):
                continue

            # 1. Capture conversation_id if present
            conv_id = payload.get("conversation_id")
            if isinstance(conv_id, str) and conv_id and conv_id != self._last_conv_id:
                self._last_conv_id = conv_id
                events.append(TelemetryEvent(event_type="conversation_id", data={"conversation_id": conv_id}))

            # 2. Check for Error
            if payload.get("error"):
                events.append(TelemetryEvent(event_type="error", data={"error": payload.get("error")}))
                continue

            # 3. Handle V1 Conduit/Patch format
            rec_type = payload.get("type")
            if rec_type == "message_stream_complete":
                events.append(TelemetryEvent(event_type="done", data={}))
                continue

            op = payload.get("o")
            path = str(payload.get("p") or "")
            target = payload.get("v")

            # Check if target is a full message snapshot inside patch
            if isinstance(target, dict) and "message" in target:
                payload = target

            # Check if path targets thoughts/reasoning
            if "thought" in path.lower() or "reasoning" in path.lower():
                if isinstance(target, list):
                    events.append(TelemetryEvent(event_type="thinking", data={"thoughts": target}))
                    continue
                elif isinstance(target, dict):
                    events.append(TelemetryEvent(event_type="thinking", data={"thoughts": [target]}))
                    continue
                elif isinstance(target, str) and target.strip():
                    events.append(TelemetryEvent(event_type="thinking", data={"thoughts": [{"summary": target}]}))
                    continue

            if isinstance(target, str) and path in ("", V1_PARTS_PATH) and op in (None, "append"):
                if self._current_content_type == "thoughts":
                    events.append(TelemetryEvent(event_type="thinking", data={"thoughts": [{"summary": target}]}))
                    continue
                if self._current_role == "tool":
                    continue
                if self._current_role == "assistant" and self._current_recipient not in (None, "all", "final"):
                    continue
                self._accumulated_text += target
                events.append(TelemetryEvent(event_type="delta", data={"text": target}))
                continue

            if isinstance(target, list) and op == "patch":
                for item in target:
                    if not isinstance(item, dict):
                        continue
                    item_path = str(item.get("p") or "")
                    if "thought" in item_path.lower() or "reasoning" in item_path.lower():
                        v = item.get("v")
                        if isinstance(v, list):
                            events.append(TelemetryEvent(event_type="thinking", data={"thoughts": v}))
                        elif isinstance(v, dict):
                            events.append(TelemetryEvent(event_type="thinking", data={"thoughts": [v]}))
                        elif isinstance(v, str) and v.strip():
                            events.append(TelemetryEvent(event_type="thinking", data={"thoughts": [{"summary": v}]}))
                        continue
                    if item_path == V1_PARTS_PATH and item.get("o") == "append" and isinstance(item.get("v"), str):
                        if self._current_content_type == "thoughts":
                            events.append(TelemetryEvent(event_type="thinking", data={"thoughts": [{"summary": item.get("v")}]}))
                            continue
                        if self._current_role == "tool":
                            continue
                        if self._current_role == "assistant" and self._current_recipient not in (None, "all", "final"):
                            continue
                        delta_val = item.get("v")
                        self._accumulated_text += delta_val
                        events.append(TelemetryEvent(event_type="delta", data={"text": delta_val}))
                    elif item_path == V1_STATUS_PATH and item.get("v") in COMPLETION_STATUSES:
                        # Do not mark stream done prematurely; intermediate steps (thinking, tools) also finish
                        pass
                continue

            # 4. Handle Classic ChatGPT Message format
            msg = payload.get("message")
            if not isinstance(msg, dict):
                continue

            author = msg.get("author") or {}
            role = author.get("role")
            recipient = msg.get("recipient")
            metadata = msg.get("metadata") or {}
            content = msg.get("content") or {}
            channel = msg.get("channel")
            status = msg.get("status")

            self._current_role = role
            self._current_recipient = recipient
            self._current_author_name = author.get("name")
            self._current_content_type = content.get("content_type")

            # Capture message ID (track assistant nodes for conversation continuity)
            msg_id = msg.get("id")
            if (
                role == "assistant"
                and isinstance(msg_id, str)
                and msg_id
                and msg_id != self._last_msg_id
                and self._current_content_type != "thoughts"
            ):
                self._last_msg_id = msg_id
                events.append(TelemetryEvent(event_type="message_id", data={"message_id": msg_id}))

            # Web search event
            if recipient == "web.run" or author.get("name") == "web.run" or "search_model_queries" in metadata:
                queries = metadata.get("search_model_queries", {}).get("queries", [])
                events.append(TelemetryEvent(event_type="web_search", data={"queries": queries}))

            # Tool call response (role == tool)
            elif role == "tool":
                invoked = metadata.get("invoked_plugin") or metadata.get("invoked_resource") or {}
                r_title = metadata.get("reasoning_title")
                parts = content.get("parts", [])
                output_text = parts[0] if parts else content.get("text", "")
                tool_call_id, active_tool = self._finish_tool_call(msg, metadata)
                effective_tool = active_tool or author.get("name") or invoked.get("title") or invoked.get("namespace") or ""
                result_data = {
                    "id": tool_call_id or msg_id,
                    "title": r_title,
                    "resource": invoked,
                    "tool": effective_tool,
                    "output": output_text,
                }
                exit_code = _extract_exit_code(output_text, metadata)
                if exit_code is not None:
                    result_data["exit_code"] = exit_code
                events.append(TelemetryEvent(
                    event_type="tool_call_finished",
                    data=result_data,
                ))

            # Tool call start (assistant invoking tool)
            elif (
                role == "assistant"
                and isinstance(recipient, str)
                and recipient not in ("all", "final")
                and content.get("content_type") != "thoughts"
            ):
                parts = content.get("parts", [])
                text_content = content.get("text", "")
                tool_payload = parts[0] if parts else text_content
                tool_label = recipient or author.get("name", "tool")
                if isinstance(tool_payload, str):
                    try:
                        p_data = json.loads(tool_payload)
                        if isinstance(p_data, dict):
                            action_name = (
                                p_data.get("action")
                                or p_data.get("name")
                                or (p_data.get("path", "").lstrip("/").split("/")[-1] if "path" in p_data else None)
                            )
                            if action_name:
                                tool_label = action_name
                    except Exception:
                        pass
                tool_call_id = self._reference_id(msg, metadata) or msg_id
                if isinstance(tool_call_id, str) and tool_call_id:
                    self._active_tool_calls[tool_call_id] = tool_label
                events.append(TelemetryEvent(
                    event_type="tool_call_started",
                    data={
                        "id": tool_call_id or msg_id,
                        "tool": tool_label,
                        "payload": tool_payload,
                        "status": status,
                    }
                ))

            # Reasoning Recap event (e.g. "Worked for 1m 41s")
            elif content.get("content_type") == "reasoning_recap":
                recap_text = content.get("content") or "Reasoning completed"
                duration_sec = metadata.get("finished_duration_sec")
                events.append(TelemetryEvent(
                    event_type="reasoning_recap",
                    data={"recap": recap_text, "duration_sec": duration_sec}
                ))

            # Thinking / Reasoning event
            elif content.get("content_type") == "thoughts" or "reasoning_title" in metadata:
                thoughts = content.get("thoughts", [])
                r_title = metadata.get("reasoning_title")
                if r_title and not thoughts:
                    thoughts = [{"summary": r_title}]
                events.append(TelemetryEvent(event_type="thinking", data={"thoughts": thoughts, "title": r_title}))

            # Commentary channel
            elif channel == "commentary":
                parts = content.get("parts", [])
                events.append(TelemetryEvent(
                    event_type="commentary",
                    data={"text": parts[0] if parts else ""}
                ))

            # Final channel or standard assistant response text
            elif channel == "final" or (role == "assistant" and channel is None):
                parts = content.get("parts", [])
                if isinstance(parts, list):
                    candidate = "".join(p for p in parts if isinstance(p, str))
                    if candidate.startswith(self._accumulated_text):
                        delta = candidate[len(self._accumulated_text):]
                        self._accumulated_text = candidate
                    elif not self._accumulated_text.startswith(candidate):
                        delta = candidate
                        self._accumulated_text += candidate
                    else:
                        delta = ""
                    if delta:
                        events.append(TelemetryEvent(event_type="delta", data={"text": delta}))

                # Parse citations if attached
                raw_citations = metadata.get("citations")
                if isinstance(raw_citations, list) and raw_citations:
                    parsed_cites = []
                    for c in raw_citations:
                        if isinstance(c, dict):
                            cm = c.get("metadata") or {}
                            if cm.get("url"):
                                parsed_cites.append({"url": cm.get("url"), "title": cm.get("title", "")})
                    if parsed_cites:
                        events.append(TelemetryEvent(event_type="citations", data={"citations": parsed_cites}))

            if (
                role == "assistant"
                and channel in ("final", None)
                and recipient in ("all", None)
                and content.get("content_type") == "text"
                and status in COMPLETION_STATUSES
            ):
                events.append(TelemetryEvent(event_type="done", data={}))
            elif status in COMPLETION_STATUSES:
                events.append(TelemetryEvent(event_type="message_completed", data={"message_id": msg_id, "role": role, "channel": channel}))

        return events

    def finish(self) -> list[TelemetryEvent]:
        try:
            rem = self._utf8.decode(b"", final=True)
            if rem:
                return self.feed(rem + "\n\n")
        except Exception:
            pass
        return []
