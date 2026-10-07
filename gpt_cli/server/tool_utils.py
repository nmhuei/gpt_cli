"""Shared gateway tool name and payload normalization helpers."""

import json
import re
import shlex
from typing import Any


def _resolve_tool_name(raw_name: str, registered: set[str]) -> tuple[str, bool]:
    """Resolve only names advertised by the caller's current tool schema."""
    if not registered:
        return raw_name, False

    raw_name = raw_name.strip()
    candidates: list[tuple[str, bool]] = [(raw_name, False)]
    parenthesized = re.search(r"\(([^()]+)\)", raw_name)
    if parenthesized:
        candidates.append((parenthesized.group(1).strip(), True))
    for candidate, unwrap in list(candidates):
        if "." in candidate:
            candidates.append((candidate.rsplit(".", 1)[1].strip(), True))

    def normalized(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")

    valid_registered = [str(name) for name in registered if name is not None]
    indexed = {normalized(name): str(name) for name in valid_registered}
    for candidate, unwrap in candidates:
        key = normalized(candidate)
        if not key:
            continue
        if key in indexed:
            return indexed[key], unwrap
        key_parts = key.split("_")
        matches = [str(name) for name in valid_registered if normalized(name).split("_")[-len(key_parts):] == key_parts]
        if len(matches) == 1:
            return matches[0], unwrap

    return raw_name, False

def _normalize_tool_payload(resolved_name: str, raw_payload: Any, should_unwrap: bool = False) -> dict[str, Any]:
    """Normalizes upstream tool payload into a valid JSON schema dictionary for harness tools."""
    if isinstance(raw_payload, dict):
        obj = dict(raw_payload)
    elif isinstance(raw_payload, str):
        try:
            obj = json.loads(raw_payload)
            if not isinstance(obj, dict):
                obj = {"input": obj}
        except json.JSONDecodeError:
            obj = {"input": raw_payload}
    else:
        obj = {}

    if should_unwrap and "args" in obj and isinstance(obj["args"], dict):
        obj = dict(obj["args"])

    r_lower = resolved_name.lower()

    # Normalization for bash / command execution
    if r_lower in ("bash", "execute_command", "sh", "terminal", "run_command"):
        if "command" not in obj:
            if "paths" in obj and isinstance(obj["paths"], list):
                p_str = " ".join(shlex.quote(str(p)) for p in obj["paths"] if p)
                obj["command"] = f"ls -la {p_str}" if p_str else "ls -la"
            elif "pattern" in obj:
                obj["command"] = f"ls -la {shlex.quote(str(obj['pattern']))}"
            else:
                has_code_field = "code" in obj or "script" in obj
                has_cmd_field = "cmd" in obj or "command" in obj
                cmd = obj.get("input") or obj.get("code") or obj.get("script") or obj.get("cmd") or ""
                if (
                    not cmd
                    and isinstance(raw_payload, str)
                    and raw_payload.strip() not in ("{}", "[]", "")
                    and not (raw_payload.strip().startswith("{") and raw_payload.strip().endswith("}"))
                ):
                    cmd = raw_payload
                if isinstance(cmd, str):
                    cmd_str = cmd.strip()
                    is_python = (
                        has_code_field
                        and not has_cmd_field
                        and bool(re.search(r"^\s*(import\s+\w+|from\s+\w+\s+import|def\s+\w+\s*\(|class\s+\w+)", cmd_str, re.MULTILINE))
                        and not cmd_str.startswith(("python", "bash", "sh"))
                    )
                    if is_python:
                        cmd_str = f"python3 -c {json.dumps(cmd_str)}"
                    obj["command"] = cmd_str
                else:
                    obj["command"] = str(cmd)
        if "description" not in obj:
            lines = str(obj.get("command", "")).strip().splitlines()
            first_line = lines[0][:60] if lines else ""
            obj["description"] = f"Run: {first_line}" if lines else "Execute command"
        # Prune non-standard keys so strict tools like Claude Code's Bash don't reject
        for k in ("paths", "query", "input", "code", "script", "cmd"):
            obj.pop(k, None)

    # Normalization for glob / directory listing
    elif r_lower in ("glob", "fs_list"):
        if "pattern" not in obj:
            d = obj.get("path") or obj.get("directory") or obj.get("dir") or "."
            d_str = str(d).rstrip("/")
            obj["pattern"] = f"{d_str}/*" if d_str and d_str != "." else "*"
        for k in ("directory", "dir"):
            obj.pop(k, None)

    # Normalization for file reading (dual path and file_path for Claude Code & dsh)
    elif r_lower in ("read_file", "read", "view_file", "view"):
        p = obj.get("file_path") or obj.get("path") or obj.get("file") or obj.get("input")
        if p:
            obj["path"] = str(p)
            obj["file_path"] = str(p)
        for k in ("file", "input"):
            obj.pop(k, None)

    # Normalization for file writing
    elif r_lower in ("write_file", "write"):
        p = obj.get("file_path") or obj.get("path") or obj.get("file") or obj.get("input")
        if p:
            obj["path"] = str(p)
            obj["file_path"] = str(p)
        if "content" not in obj:
            obj["content"] = obj.get("text") or obj.get("input") or ""
        for k in ("file", "input", "text"):
            obj.pop(k, None)

    # Normalization for file editing
    elif r_lower in ("edit_file", "edit", "str_replace_editor"):
        p = obj.get("file_path") or obj.get("path") or obj.get("file")
        if p:
            obj["path"] = str(p)
            obj["file_path"] = str(p)
        obj.pop("file", None)

    return obj



