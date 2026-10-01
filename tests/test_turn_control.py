import asyncio
from pathlib import Path

from gpt_cli.session.live_turns import LiveTurnRegistry, TurnOwner
from gpt_cli.session.turn_control import TurnControlServer, request_steer


def test_control_socket_delivers_accepted_steer_to_live_owner(tmp_path: Path):
    """Breaking the local socket handoff would make a queued steer appear accepted but vanish."""
    async def run() -> None:
        registry = LiveTurnRegistry(tmp_path)
        owner = TurnOwner.create_for_current_process("sess_one", tmp_path / "turn.sock")
        registry.claim(owner)
        accepted: list[tuple[str, str]] = []

        async def on_steer(request_id: str, prompt: str) -> dict[str, str]:
            accepted.append((request_id, prompt))
            return {"status": "queued", "session_id": "sess_one", "message": "stored"}

        server = TurnControlServer(owner, registry, on_steer)
        await server.start()
        try:
            response = await request_steer(
                Path(owner.socket_path), owner.owner_id, "req_1", "check tests first"
            )
        finally:
            await server.close()

        assert response == {"status": "queued", "session_id": "sess_one", "message": "stored"}
        assert accepted == [("req_1", "check tests first")]

    asyncio.run(run())


def test_control_socket_rejects_wrong_owner_without_invoking_callback(tmp_path: Path):
    """Accepting a stale owner id could steer a replacement process."""
    async def run() -> None:
        registry = LiveTurnRegistry(tmp_path)
        owner = TurnOwner.create_for_current_process("sess_one", tmp_path / "turn.sock")
        registry.claim(owner)
        called = False

        async def on_steer(request_id: str, prompt: str) -> dict[str, str]:
            nonlocal called
            called = True
            return {"status": "queued", "session_id": "sess_one", "message": "stored"}

        server = TurnControlServer(owner, registry, on_steer)
        await server.start()
        try:
            response = await request_steer(Path(owner.socket_path), "wrong-owner", "req_1", "text")
        finally:
            await server.close()

        assert response["status"] == "rejected"
        assert called is False

    asyncio.run(run())
