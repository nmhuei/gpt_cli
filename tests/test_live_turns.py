import json
from pathlib import Path

import pytest

from gpt_cli.session.live_turns import (
    LiveTurnRegistry,
    SessionBusyError,
    LiveTurnRegistryCorruptionError,
    TurnOwner,
    NoLiveTurnError,
)


def _owner(session_id: str, runtime_dir: Path) -> TurnOwner:
    return TurnOwner.create_for_current_process(
        session_id=session_id,
        socket_path=runtime_dir / f"{session_id}.sock",
    )


def test_same_live_session_rejects_second_owner(tmp_path: Path):
    """Dropping the lease check would allow two writers to branch one DAG."""
    registry = LiveTurnRegistry(tmp_path)
    registry.claim(_owner("sess_one", tmp_path))

    with pytest.raises(SessionBusyError, match="sess_one"):
        registry.claim(_owner("sess_one", tmp_path))


def test_live_queue_is_fifo_and_survives_registry_reload(tmp_path: Path):
    """Changing queue order or forgetting a disk write loses user steering."""
    registry = LiveTurnRegistry(tmp_path)
    registry.claim(_owner("sess_one", tmp_path))
    registry.enqueue("sess_one", request_id="r1", prompt="first")
    registry.enqueue("sess_one", request_id="r2", prompt="second")

    reloaded = LiveTurnRegistry(tmp_path)
    first = reloaded.claim_next_queued("sess_one")
    assert first is not None
    assert (first.request_id, first.prompt, first.state) == ("r1", "first", "claimed")
    reloaded.complete_queued("sess_one", "r1")

    second = reloaded.claim_next_queued("sess_one")
    assert second is not None
    assert (second.request_id, second.prompt, second.state) == ("r2", "second", "claimed")


def test_stale_live_owner_is_removed_before_a_replacement_claims(tmp_path: Path):
    """A dead CLI process must not leave a session permanently busy."""
    registry = LiveTurnRegistry(tmp_path)
    old_owner = _owner("sess_one", tmp_path)
    registry.claim(old_owner)

    state_file = tmp_path / "live_turns.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["turns"]["sess_one"]["owner"]["pid"] = 999_999_999
    state_file.write_text(json.dumps(state), encoding="utf-8")

    replacement = _owner("sess_one", tmp_path)
    LiveTurnRegistry(tmp_path).claim(replacement)

    live = LiveTurnRegistry(tmp_path).get_live("sess_one")
    assert live is not None
    assert live.owner.owner_id == replacement.owner_id


@pytest.mark.parametrize("operation", ["enqueue", "claim_next_queued"])
def test_failed_queue_operation_persists_stale_owner_cleanup(tmp_path: Path, operation: str):
    registry = LiveTurnRegistry(tmp_path)
    registry.claim(_owner("sess_one", tmp_path))

    state_file = tmp_path / "live_turns.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["turns"]["sess_one"]["owner"]["pid"] = 999_999_999
    state_file.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(NoLiveTurnError):
        if operation == "enqueue":
            LiveTurnRegistry(tmp_path).enqueue("sess_one", "request", "steer")
        else:
            LiveTurnRegistry(tmp_path).claim_next_queued("sess_one")

    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert "sess_one" not in persisted["turns"]


def test_corrupt_live_registry_blocks_delete_if_idle_and_preserves_bytes(tmp_path: Path):
    """Corrupt live state must never look idle during deletion."""
    state_file = tmp_path / "live_turns.json"
    corrupt = "{not-json"
    state_file.write_text(corrupt, encoding="utf-8")
    registry = LiveTurnRegistry(tmp_path)

    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.delete_if_idle("sess_one", lambda: True)

    assert state_file.read_text(encoding="utf-8") == corrupt


def test_corrupt_live_registry_blocks_claim_and_preserves_bytes(tmp_path: Path):
    """A corrupt live registry must not be overwritten by a new claim."""
    state_file = tmp_path / "live_turns.json"
    corrupt = "{not-json"
    state_file.write_text(corrupt, encoding="utf-8")
    registry = LiveTurnRegistry(tmp_path)

    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.claim(_owner("sess_one", tmp_path))

    assert state_file.read_text(encoding="utf-8") == corrupt


def test_malformed_live_turn_entry_blocks_delete_if_idle_and_preserves_bytes(tmp_path: Path):
    """A malformed live owner entry must not be mistaken for an idle session."""
    state_file = tmp_path / "live_turns.json"
    corrupt = '{"turns": {"sess_one": {"owner": "bad"}}}'
    state_file.write_text(corrupt, encoding="utf-8")
    registry = LiveTurnRegistry(tmp_path)

    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.delete_if_idle("sess_one", lambda: True)

    assert state_file.read_text(encoding="utf-8") == corrupt


def test_malformed_live_turn_entry_blocks_claim_and_preserves_bytes(tmp_path: Path):
    """A malformed live owner entry must not be overwritten by claim."""
    state_file = tmp_path / "live_turns.json"
    corrupt = '{"turns": {"sess_one": {"owner": "bad"}}}'
    state_file.write_text(corrupt, encoding="utf-8")
    registry = LiveTurnRegistry(tmp_path)

    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.claim(_owner("sess_one", tmp_path))

    assert state_file.read_text(encoding="utf-8") == corrupt


def test_live_entry_missing_session_id_blocks_delete_and_preserves_bytes(tmp_path: Path):
    """A structurally broken live entry must not be interpreted as idle."""
    state_file = tmp_path / "live_turns.json"
    corrupt = json.dumps({"turns": {"sess_one": {"owner": {}}}})
    state_file.write_text(corrupt, encoding="utf-8")
    registry = LiveTurnRegistry(tmp_path)

    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.delete_if_idle("sess_one", lambda: True)

    assert state_file.read_text(encoding="utf-8") == corrupt


def test_malformed_live_entry_blocks_list_and_get_without_rewrite(tmp_path: Path):
    """Read paths must not silently accept malformed live entries."""
    state_file = tmp_path / "live_turns.json"
    corrupt = '{"turns": {"sess_one": {"owner": {}}}}'
    state_file.write_text(corrupt, encoding="utf-8")
    registry = LiveTurnRegistry(tmp_path)

    from gpt_cli.session.live_turns import LiveTurnRegistryCorruptionError

    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.get_live("sess_one")
    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.list_live()

    assert state_file.read_text(encoding="utf-8") == corrupt


def test_non_object_turn_entry_is_corruption_not_stale_cleanup(tmp_path: Path):
    """Malformed turn entries must never be purged as stale owners."""
    state_file = tmp_path / "live_turns.json"
    corrupt = json.dumps({"turns": {"sess_one": "not-an-object"}})
    state_file.write_text(corrupt, encoding="utf-8")
    registry = LiveTurnRegistry(tmp_path)

    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.cleanup_stale()

    assert state_file.read_text(encoding="utf-8") == corrupt


def test_non_object_named_turn_blocks_get_live_without_rewrite(tmp_path: Path):
    """A named non-object turn entry is corruption, not a missing live owner."""
    from gpt_cli.session.live_turns import LiveTurnRegistry, LiveTurnRegistryCorruptionError

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    state = runtime / "live_turns.json"
    original = '{"turns": {"sess_one": "invalid"}}'
    state.write_text(original, encoding="utf-8")

    registry = LiveTurnRegistry(runtime)
    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.get_live("sess_one")

    assert state.read_text(encoding="utf-8") == original


def test_non_object_turn_blocks_list_live_without_rewrite(tmp_path: Path):
    """Listing live turns must not silently discard malformed entries."""
    from gpt_cli.session.live_turns import LiveTurnRegistry, LiveTurnRegistryCorruptionError

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    state = runtime / "live_turns.json"
    original = '{"turns": {"sess_one": []}}'
    state.write_text(original, encoding="utf-8")

    registry = LiveTurnRegistry(runtime)
    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.list_live()

    assert state.read_text(encoding="utf-8") == original


def test_require_raw_rejects_non_object_turn_entry_without_treating_missing(tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    state_file = runtime_dir / "live_turns.json"
    state_file.write_text('{"turns":{"sess_bad":[]}}', encoding="utf-8")
    registry = LiveTurnRegistry(runtime_dir)

    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.enqueue("sess_bad", "req", "prompt")

    assert state_file.read_text(encoding="utf-8") == '{"turns":{"sess_bad":[]}}'


def test_update_socket_path_rejects_non_object_turn_entry(tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    state_file = runtime_dir / "live_turns.json"
    state_file.write_text('{"turns":{"sess_bad":null}}', encoding="utf-8")
    registry = LiveTurnRegistry(runtime_dir)

    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.update_socket_path("sess_bad", "owner", "/tmp/socket")

    assert state_file.read_text(encoding="utf-8") == '{"turns":{"sess_bad":null}}'


def test_release_non_object_turn_entry_is_corruption_not_missing(tmp_path):
    """release must not hide malformed persisted turns as an absent owner."""
    import pytest
    from gpt_cli.session.live_turns import LiveTurnRegistry, LiveTurnRegistryCorruptionError

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    state = runtime / "live_turns.json"
    corrupt = '{"turns":{"sess_bad":[]}}'
    state.write_text(corrupt, encoding="utf-8")

    registry = LiveTurnRegistry(runtime)
    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.release("sess_bad", "owner")

    assert state.read_text(encoding="utf-8") == corrupt


def test_non_object_queue_entry_is_registry_corruption(tmp_path):
    """Malformed queue entries must not be silently discarded."""
    import json

    from gpt_cli.session.live_turns import LiveTurnRegistry, LiveTurnRegistryCorruptionError

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    registry = LiveTurnRegistry(runtime)
    state = {
        "turns": {
            "sess_bad_queue": {
                "session_id": "sess_bad_queue",
                "owner": {
                    "owner_id": "owner",
                    "pid": 1,
                    "started_at": "now",
                    "socket_path": "sock",
                },
                "queue": ["not-an-object"],
            }
        }
    }
    raw = json.dumps(state)
    (runtime / "live_turns.json").write_text(raw, encoding="utf-8")

    try:
        registry.get_live("sess_bad_queue")
    except LiveTurnRegistryCorruptionError:
        pass
    else:
        raise AssertionError("expected corrupt queue entry to fail closed")

    assert (runtime / "live_turns.json").read_text(encoding="utf-8") == raw


def test_update_socket_path_rejects_non_object_owner_and_preserves_bytes(tmp_path):
    from gpt_cli.session.live_turns import LiveTurnRegistry, LiveTurnRegistryCorruptionError

    state_dir = tmp_path / "runtime"
    state_dir.mkdir()
    original = '{"turns":{"sess":{"owner":"corrupt"}}}'
    state = state_dir / "live_turns.json"
    state.write_text(original, encoding="utf-8")
    registry = LiveTurnRegistry(state_dir)

    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.update_socket_path("sess", "owner", "/tmp/socket")

    assert state.read_text(encoding="utf-8") == original


def test_empty_existing_live_registry_blocks_delete_if_idle_and_preserves_bytes(tmp_path: Path):
    """An existing empty registry file is corruption, not an idle registry."""
    state_file = tmp_path / "live_turns.json"
    state_file.write_text("", encoding="utf-8")
    registry = LiveTurnRegistry(tmp_path)

    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.delete_if_idle("sess_one", lambda: True)

    assert state_file.read_text(encoding="utf-8") == ""


def test_invalid_utf8_live_registry_blocks_delete_if_idle_and_preserves_bytes(tmp_path: Path):
    """Invalid registry encoding must never look like an idle session."""
    state_file = tmp_path / "live_turns.json"
    corrupt = b"{\xff\xfe"
    state_file.write_bytes(corrupt)
    registry = LiveTurnRegistry(tmp_path)

    with pytest.raises(LiveTurnRegistryCorruptionError):
        registry.delete_if_idle("sess_one", lambda: True)

    assert state_file.read_bytes() == corrupt
