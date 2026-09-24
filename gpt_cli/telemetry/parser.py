import codecs
import json
from typing import Any
from .events import TelemetryEvent

COMPLETION_STATUSES = frozenset({"finished_successfully", "finished", "complete"})
V1_PARTS_PATH = "/message/content/parts/0"
V1_STATUS_PATH = "/message/status"

class SSETelemetryParser:
    """Incremental, stream-safe SSE Telemetry and Text Delta Parser."""

    def __init__(self):
        self._buffer = ""
        self._utf8 = codecs.getincrementaldecoder("utf-8")()
        self._accumulated_text = ""
        self._last_conv_id: str | None = None
        self._last_msg_id: str | None = None

    def feed(self, chunk: bytes | str) -> list[TelemetryEvent]:
        if isinstance(chunk, bytes):
            text = self._utf8.decode(chunk, final=False)
        else:
            text = chunk

        self._buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        events: list[TelemetryEvent] = []

        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            lines = frame.split("\n")
            for line in lines:
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
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
                path = payload.get("p") or ""
                target = payload.get("v")

                if isinstance(target, str) and path in ("", V1_PARTS_PATH) and op in (None, "append"):
                    self._accumulated_text += target
                    events.append(TelemetryEvent(event_type="delta", data={"text": target}))
                    continue

                if isinstance(target, list) and op == "patch":
                    for item in target:
                        if not isinstance(item, dict):
                            continue
                        if item.get("p") == V1_PARTS_PATH and item.get("o") == "append" and isinstance(item.get("v"), str):
                            delta_val = item.get("v")
                            self._accumulated_text += delta_val
                            events.append(TelemetryEvent(event_type="delta", data={"text": delta_val}))
                        elif item.get("p") == V1_STATUS_PATH and item.get("v") in COMPLETION_STATUSES:
                            events.append(TelemetryEvent(event_type="done", data={}))
                    continue

                # 4. Handle Classic ChatGPT Message format
                msg = payload.get("message")
                if not isinstance(msg, dict):
                    if payload.get("status") in COMPLETION_STATUSES:
                        events.append(TelemetryEvent(event_type="done", data={}))
                    continue

                # Capture message ID
                msg_id = msg.get("id")
                if isinstance(msg_id, str) and msg_id and msg_id != self._last_msg_id:
                    self._last_msg_id = msg_id
                    events.append(TelemetryEvent(event_type="message_id", data={"message_id": msg_id}))

                author = msg.get("author") or {}
                role = author.get("role")
                recipient = msg.get("recipient")
                metadata = msg.get("metadata") or {}
                content = msg.get("content") or {}
                channel = msg.get("channel")
                status = msg.get("status")

                # Web search event
                if recipient == "web.run" or author.get("name") == "web.run" or "search_model_queries" in metadata:
                    queries = metadata.get("search_model_queries", {}).get("queries", [])
                    events.append(TelemetryEvent(event_type="web_search", data={"queries": queries}))

                # Tool call event
                elif recipient in {"functions.exec", "host_run_command", "bash", "python"} or role == "tool":
                    parts = content.get("parts", [])
                    events.append(TelemetryEvent(
                        event_type="tool_call_started",
                        data={
                            "tool": recipient or author.get("name", "unknown"),
                            "payload": parts[0] if parts else ""
                        }
                    ))

                # Thinking / Reasoning event
                elif content.get("content_type") == "thoughts":
                    thoughts = content.get("thoughts", [])
                    events.append(TelemetryEvent(event_type="thinking", data={"thoughts": thoughts}))

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

                if status in COMPLETION_STATUSES:
                    events.append(TelemetryEvent(event_type="done", data={}))

        return events

    def finish(self) -> list[TelemetryEvent]:
        try:
            rem = self._utf8.decode(b"", final=True)
            if rem:
                return self.feed(rem + "\n\n")
        except Exception:
            pass
        return []
