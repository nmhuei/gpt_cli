"""Private Unix-socket control channel for a process owning a live Wire turn."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .live_turns import LiveTurnRegistry, TurnOwner


ControlResponse = dict[str, str]
SteerHandler = Callable[[str, str], Awaitable[ControlResponse]]


class TurnControlServer:
    def __init__(self, owner: TurnOwner, registry: LiveTurnRegistry, on_steer: SteerHandler):
        self.owner = owner
        self.registry = registry
        self.on_steer = on_steer
        self._server: asyncio.AbstractServer | None = None

    def _response(self, status: str, message: str) -> ControlResponse:
        return {"status": status, "session_id": self.owner.session_id, "message": message}

    def _owns_live_record(self) -> bool:
        live = self.registry.get_live(self.owner.session_id)
        return live is not None and live.owner.owner_id == self.owner.owner_id

    async def start(self) -> None:
        path = Path(self.owner.socket_path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        if path.exists():
            path.unlink()
        self._server = await asyncio.start_unix_server(self._handle_client, path=str(path))

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        try:
            Path(self.owner.socket_path).unlink(missing_ok=True)
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
            await writer.wait_closed()


async def _send_request(socket_path: Path, request: dict[str, Any]) -> ControlResponse:
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
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
        await writer.wait_closed()


async def request_steer(socket_path: Path, owner_id: str, request_id: str, prompt: str) -> ControlResponse:
    return await _send_request(socket_path, {
        "type": "steer",
        "owner_id": owner_id,
        "request_id": request_id,
        "prompt": prompt,
    })


async def ping_owner(socket_path: Path, owner_id: str) -> ControlResponse:
    return await _send_request(socket_path, {"type": "ping", "owner_id": owner_id})
