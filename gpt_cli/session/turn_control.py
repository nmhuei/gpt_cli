"""Private Unix-socket control channel for a process owning a live Wire turn."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .live_turns import LiveTurnRegistry, TurnOwner


ControlResponse = dict[str, str]
SteerHandler = Callable[[str, str], Awaitable[ControlResponse]]

MAX_UNIX_SOCKET_PATH_LEN = 103


def get_fallback_socket_dir() -> Path:
    """Return a deterministic per-UID directory with mode 0700 for control sockets."""
    custom_dir = os.environ.get("GPT_CLI_SOCKET_DIR")
    if custom_dir:
        d = Path(custom_dir)
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            d.chmod(0o700)
        except OSError:
            pass
        return d

    uid = os.getuid() if hasattr(os, "getuid") else 0
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime and Path(xdg_runtime).is_dir() and len(xdg_runtime) <= 60:
        base_dir = Path(xdg_runtime) / "gpt_cli"
    elif Path(f"/run/user/{uid}").is_dir():
        base_dir = Path(f"/run/user/{uid}") / "gpt_cli"
    else:
        base_dir = Path(tempfile.gettempdir()) / f"gpt_cli_{uid}"

    base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        base_dir.chmod(0o700)
    except OSError:
        pass
    return base_dir


def resolve_socket_path(
    raw_path: Path | str,
    session_id: str | None = None,
    *,
    force_fallback: bool = False,
) -> Path:
    """Return raw_path if within AF_UNIX limits; otherwise return a deterministic short fallback path."""
    path = Path(raw_path)
    if not force_fallback and len(str(path).encode("utf-8")) <= MAX_UNIX_SOCKET_PATH_LEN:
        return path

    sess = session_id or path.stem
    clean_sess = re.sub(r"[^a-zA-Z0-9_]", "", sess)[:8] or "turn"
    norm_path = os.path.abspath(str(path))
    h = hashlib.sha256(norm_path.encode("utf-8")).hexdigest()[:16]
    fallback_dir = get_fallback_socket_dir()
    return fallback_dir / f"ctrl_{clean_sess}_{h}.sock"


class TurnControlServer:
    resolve_socket_path = staticmethod(resolve_socket_path)
    get_fallback_socket_dir = staticmethod(get_fallback_socket_dir)

    def __init__(self, owner: TurnOwner, registry: LiveTurnRegistry, on_steer: SteerHandler):
        self.owner = owner
        self.registry = registry
        self.on_steer = on_steer
        self._server: asyncio.AbstractServer | None = None
        self._original_socket_path: Path = Path(self.owner.socket_path)
        self._actual_socket_path: Path = self.resolve_socket_path(
            self._original_socket_path, self.owner.session_id
        )

    def _response(self, status: str, message: str) -> ControlResponse:
        return {"status": status, "session_id": self.owner.session_id, "message": message}

    def _owns_live_record(self) -> bool:
        live = self.registry.get_live(self.owner.session_id)
        return live is not None and live.owner.owner_id == self.owner.owner_id

    async def start(self) -> None:
        original_path = self._original_socket_path
        resolved_path = self._actual_socket_path

        if resolved_path == original_path:
            resolved_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                resolved_path.parent.chmod(0o700)
            except OSError:
                pass

        if resolved_path.exists():
            resolved_path.unlink()

        try:
            self._server = await asyncio.start_unix_server(self._handle_client, path=str(resolved_path))
        except OSError as exc:
            is_too_long = (
                "path too long" in str(exc).lower()
                or getattr(exc, "errno", None) == getattr(errno, "ENAMETOOLONG", 36)
            )
            is_permission = getattr(exc, "errno", None) in {
                getattr(errno, "EACCES", 13),
                getattr(errno, "EPERM", 1),
            }
            if (is_too_long or is_permission) and resolved_path == original_path:
                if is_permission:
                    uid = os.getuid() if hasattr(os, "getuid") else 0
                    fallback_dir = Path(tempfile.gettempdir()) / f"gpt_cli_{uid}"
                    fallback_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                    try:
                        fallback_dir.chmod(0o700)
                    except OSError:
                        pass
                    resolved_path = fallback_dir / f"ctrl_{self.owner.session_id[:8]}.sock"
                else:
                    resolved_path = self.resolve_socket_path(
                        original_path, self.owner.session_id, force_fallback=True
                    )
                if resolved_path.exists():
                    resolved_path.unlink()
                self._server = await asyncio.start_unix_server(self._handle_client, path=str(resolved_path))
            else:
                raise

        try:
            os.chmod(resolved_path, 0o600)
        except OSError:
            pass

        self._actual_socket_path = resolved_path
        if resolved_path != original_path:
            self.owner.socket_path = str(resolved_path)
            if self.registry is not None and hasattr(self.registry, "update_socket_path"):
                self.registry.update_socket_path(
                    self.owner.session_id, self.owner.owner_id, str(resolved_path)
                )

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        paths_to_clean = {
            self._actual_socket_path,
            self._original_socket_path,
            Path(self.owner.socket_path) if self.owner else None,
        }
        for p in paths_to_clean:
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        response: ControlResponse
        try:
            raw = await reader.readline()
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            if request.get("owner_id") != self.owner.owner_id or not self._owns_live_record():
                response = self._response("rejected", "Live turn owner does not match")
            elif request.get("type") == "ping":
                response = self._response("ok", self.owner.owner_id)
            elif request.get("type") != "steer":
                response = self._response("rejected", "Unsupported control request")
            else:
                request_id = request.get("request_id")
                prompt = request.get("prompt")
                if not isinstance(request_id, str) or not request_id:
                    response = self._response("rejected", "Steer request id is required")
                elif not isinstance(prompt, str) or not prompt.strip():
                    response = self._response("rejected", "Steer prompt must not be empty")
                else:
                    response = await self.on_steer(request_id, prompt)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, asyncio.LimitOverrunError):
            response = self._response("rejected", "Malformed control request")
        except Exception as exc:
            response = self._response("rejected", f"Control request failed: {exc}")
        writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        try:
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def _send_request(socket_path: Path, request: dict[str, Any]) -> ControlResponse:
    target_path = resolve_socket_path(socket_path)
    reader, writer = await asyncio.open_unix_connection(str(target_path))
    try:
        writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
        raw = await reader.readline()
        response = json.loads(raw.decode("utf-8"))
        if not isinstance(response, dict) or not all(isinstance(value, str) for value in response.values()):
            raise ValueError("Malformed control response")
        return response
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def request_steer(socket_path: Path, owner_id: str, request_id: str, prompt: str) -> ControlResponse:
    return await _send_request(socket_path, {
        "type": "steer",
        "owner_id": owner_id,
        "request_id": request_id,
        "prompt": prompt,
    })


async def ping_owner(socket_path: Path, owner_id: str) -> ControlResponse:
    return await _send_request(socket_path, {"type": "ping", "owner_id": owner_id})
