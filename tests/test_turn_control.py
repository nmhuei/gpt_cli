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


def test_control_socket_long_runtime_path_uses_deterministic_short_fallback(tmp_path: Path):
    """AF_UNIX path limit (>104-108 chars) must fall back to a per-UID 0700 directory and succeed."""
    import os
    import stat

    async def run() -> None:
        long_dir = tmp_path / ("nested_long_runtime_" + "x" * 90)
        long_dir.mkdir(parents=True, exist_ok=True)
        long_sock_path = long_dir / "sess_long_path_turn.sock"
        assert len(str(long_sock_path).encode("utf-8")) > 103

        registry = LiveTurnRegistry(long_dir)
        owner = TurnOwner.create_for_current_process("sess_long_path", long_sock_path)
        registry.claim(owner)

        accepted: list[tuple[str, str]] = []

        async def on_steer(request_id: str, prompt: str) -> dict[str, str]:
            accepted.append((request_id, prompt))
            return {"status": "queued", "session_id": "sess_long_path", "message": "stored"}

        server = TurnControlServer(owner, registry, on_steer)
        await server.start()

        short_sock = Path(owner.socket_path)
        try:
            assert short_sock != long_sock_path
            assert len(str(short_sock).encode("utf-8")) <= 103
            assert short_sock.exists()
            assert short_sock.is_socket()

            # Ensure socket is NOT directly in shared /tmp
            assert short_sock.parent != Path("/tmp")
            # Ensure socket parent is a per-UID directory with mode 0700
            parent_mode = stat.S_IMODE(short_sock.parent.stat().st_mode)
            assert parent_mode == 0o700
            assert str(os.getuid()) in str(short_sock.parent)

            # Ensure no unnecessary symlink was created at long_sock_path
            assert not long_sock_path.exists()
            assert not long_sock_path.is_symlink()

            # Registry record should reflect the short socket path
            live_record = registry.get_live("sess_long_path")
            assert live_record is not None
            assert live_record.owner.socket_path == str(short_sock)

            # Steering directly via the updated short socket path
            resp1 = await request_steer(short_sock, owner.owner_id, "req_short", "steer short")
            assert resp1["status"] == "queued"
            assert accepted[-1] == ("req_short", "steer short")

            # Steering via the original long path should automatically resolve and succeed
            resp2 = await request_steer(long_sock_path, owner.owner_id, "req_long", "steer long")
            assert resp2["status"] == "queued"
            assert accepted[-1] == ("req_long", "steer long")
        finally:
            await server.close()

        # Fallback socket must be safely cleaned up and no long-path file exists
        assert not short_sock.exists()
        assert not long_sock_path.exists()

    asyncio.run(run())


def test_resolve_socket_path_deterministic():
    import os
    import stat
    from gpt_cli.session.turn_control import resolve_socket_path

    short_path = Path("/tmp/short.sock")
    assert resolve_socket_path(short_path) == short_path

    long_path = Path("/home/light/very/long/path/" + "y" * 120 + "/sess_123.sock")
    resolved1 = resolve_socket_path(long_path, "sess_123")
    resolved2 = resolve_socket_path(long_path, "sess_123")

    assert resolved1 == resolved2
    assert len(str(resolved1).encode("utf-8")) <= 103
    assert resolve_socket_path(resolved1) == resolved1
    assert resolved1.parent != Path("/tmp")
    assert stat.S_IMODE(resolved1.parent.stat().st_mode) == 0o700
    assert str(os.getuid()) in str(resolved1.parent)


def test_control_socket_permission_failure_uses_private_uid_fallback(monkeypatch, tmp_path: Path):
    """A bind permission failure should retry in the private per-UID socket directory."""
    import errno

    async def run() -> None:
        registry = LiveTurnRegistry(tmp_path)
        owner = TurnOwner.create_for_current_process("sess_perm", tmp_path / "turn.sock")
        registry.claim(owner)
        calls = []
        real_start = asyncio.start_unix_server

        async def fake_start(handler, *, path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError(errno.EACCES, "permission denied")
            return await real_start(handler, path=path)

        monkeypatch.setattr(asyncio, "start_unix_server", fake_start)
        server = TurnControlServer(owner, registry, lambda *_: None)
        await server.start()
        try:
            socket_path = Path(owner.socket_path)
            assert len(calls) == 2
            assert socket_path.exists()
            assert socket_path != tmp_path / "turn.sock"
            assert str(os.getuid()) in str(socket_path.parent)
        finally:
            await server.close()

    import os
    asyncio.run(run())
