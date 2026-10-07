import pytest
from pathlib import Path
from gpt_cli.session.models import SessionRecord
from gpt_cli.session.registry import SessionLookupError, SessionRegistry, SessionRegistryCorruptionError


def test_corrupt_registry_raises_and_preserves_bytes(tmp_path: Path):
    """A malformed registry must stop startup and leave evidence untouched."""
    reg = SessionRegistry(storage_dir=tmp_path)
    reg.create_session(local_id="stale_session")

    corrupted = b"{not valid json"
    reg.storage_file.write_bytes(corrupted)

    with pytest.raises(SessionRegistryCorruptionError, match="Corrupt session registry"):
        SessionRegistry(storage_dir=tmp_path)

    assert reg.storage_file.read_bytes() == corrupted


def test_empty_existing_registry_is_corruption_and_preserves_bytes(tmp_path: Path):
    registry_file = tmp_path / "tool_sessions.json"
    registry_file.write_bytes(b"")

    with pytest.raises(SessionRegistryCorruptionError, match="Corrupt session registry"):
        SessionRegistry(storage_dir=tmp_path)

    assert registry_file.read_bytes() == b""


def test_session_creation_reports_storage_failure_and_preserves_previous_registry(tmp_path: Path, monkeypatch):
    reg = SessionRegistry(storage_dir=tmp_path)
    reg.create_session(local_id="sess_existing")
    saved_bytes = reg.storage_file.read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError("disk is read-only")

    with monkeypatch.context() as patch:
        patch.setattr("gpt_cli.session.registry.os.replace", fail_replace)
        with pytest.raises(OSError, match="Failed to persist session registry"):
            reg.create_session(local_id="sess_unsaved")

    assert reg.storage_file.read_bytes() == saved_bytes
    reloaded = SessionRegistry(storage_dir=tmp_path)
    assert reloaded.get_session("sess_existing") is not None
    assert reloaded.get_session("sess_unsaved") is None


def test_registry_write_fails_closed_when_process_lock_cannot_be_acquired(tmp_path: Path, monkeypatch):
    registry = SessionRegistry(storage_dir=tmp_path)

    def fail_flock(*_args, **_kwargs):
        raise OSError("lock unavailable")

    monkeypatch.setattr("gpt_cli.session.registry.fcntl.flock", fail_flock)

    with pytest.raises(OSError, match="Failed to acquire session registry lock") as exc_info:
        registry.create_session(local_id="must_not_write")

    assert isinstance(exc_info.value.__cause__, OSError)
    assert str(exc_info.value.__cause__) == "lock unavailable"
    assert not registry.storage_file.exists()

def test_session_creation_and_continuity(tmp_path: Path):
    reg = SessionRegistry(storage_dir=tmp_path)

    # 1. Initially no active session
    assert reg.get_active() is None

    # 2. Create a session
    sess = reg.get_or_create(tag="ctf-worker-1")
    assert sess.local_id.startswith("sess_")
    assert sess.tag == "ctf-worker-1"
    assert sess.conversation_id is None
    assert sess.last_message_id is None
    assert sess.turns == 0

    # Active should now point to this session
    active = reg.get_active()
    assert active is not None
    assert active.local_id == sess.local_id

    # 3. Update turn with upstream OpenAI conversation_id and leaf message_id
    reg.update_turn(
        local_id=sess.local_id,
        conversation_id="conv_upstream_12345",
        last_message_id="msg_leaf_99999",
        model="gpt-5-6-thinking"
    )

    # Verify continuity
    reloaded = reg.get_session(sess.local_id)
    assert reloaded is not None
    assert reloaded.conversation_id == "conv_upstream_12345"
    assert reloaded.last_message_id == "msg_leaf_99999"
    assert reloaded.turns == 1
    assert reloaded.model == "gpt-5-6-thinking"

def test_multiple_sessions_management(tmp_path: Path):
    reg = SessionRegistry(storage_dir=tmp_path)
    s1 = reg.create_session(local_id="worker_a", tag="tag_a")
    s2 = reg.create_session(local_id="worker_b", tag="tag_b")

    all_sessions = reg.list_sessions()
    ids = [s.local_id for s in all_sessions]
    assert "worker_a" in ids
    assert "worker_b" in ids

    # Test setting active
    reg.set_active("worker_a")
    assert reg.get_active().local_id == "worker_a"
    reg.set_active("worker_b")
    assert reg.get_active().local_id == "worker_b"

def test_session_persistence(tmp_path: Path):
    reg1 = SessionRegistry(storage_dir=tmp_path)
    s = reg1.create_session(local_id="persist_test", tag="persist")
    reg1.update_turn(local_id="persist_test", conversation_id="conv_p1", last_message_id="msg_p1")

    # Load in new instance
    reg2 = SessionRegistry(storage_dir=tmp_path)
    loaded = reg2.get_session("persist_test")
    assert loaded is not None
    assert loaded.conversation_id == "conv_p1"
    assert loaded.last_message_id == "msg_p1"


def test_require_session_rejects_missing_active_and_unknown_id(tmp_path: Path):
    """Removing strict resume validation must fail this input-contract test."""
    reg = SessionRegistry(storage_dir=tmp_path)

    with pytest.raises(SessionLookupError, match="No active local session"):
        reg.require_session(None)
    with pytest.raises(SessionLookupError, match="sess_missing"):
        reg.require_session("sess_missing")


def test_require_session_returns_existing_active_or_named_session(tmp_path: Path):
    """Resume resolution must never manufacture a local session."""
    reg = SessionRegistry(storage_dir=tmp_path)
    session = reg.create_session(local_id="sess_known")

    assert reg.require_session(None).local_id == session.local_id
    assert reg.require_session("sess_known").local_id == session.local_id


def test_checkpoint_preserves_pointer_without_incrementing_completed_turns(tmp_path: Path):
    reg = SessionRegistry(storage_dir=tmp_path)
    session = reg.create_session()

    reg.checkpoint(session.local_id, "conv_live", "node_live", "gpt-5-6-thinking")

    saved = reg.get_session(session.local_id)
    assert saved is not None
    assert (saved.conversation_id, saved.last_message_id, saved.model, saved.turns) == (
        "conv_live", "node_live", "gpt-5-6-thinking", 0,
    )

def test_concurrent_session_registry_writers(tmp_path: Path):
    import concurrent.futures
    storage_dir = tmp_path / "concurrent_sessions"

    def writer_task(idx: int) -> int:
        reg = SessionRegistry(storage_dir=storage_dir)
        reg.create_session(local_id=f"sess_{idx}", tag=f"tag_{idx}")
        return idx

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(writer_task, range(20)))

    verify_reg = SessionRegistry(storage_dir=storage_dir)
    sessions = verify_reg.list_sessions()
    assert len(sessions) == 20, f"Expected 20 sessions, got {len(sessions)}"


def test_wrong_root_shape_raises_and_preserves_bytes(tmp_path: Path):
    corrupted = b"[]"
    registry_file = tmp_path / "tool_sessions.json"
    registry_file.write_bytes(corrupted)

    with pytest.raises(SessionRegistryCorruptionError, match="expected object root"):
        SessionRegistry(storage_dir=tmp_path)

    assert registry_file.read_bytes() == corrupted


def test_wrong_sessions_shape_raises_and_preserves_bytes(tmp_path: Path):
    corrupted = b'{"active_id": null, "sessions": []}'
    registry_file = tmp_path / "tool_sessions.json"
    registry_file.write_bytes(corrupted)

    with pytest.raises(SessionRegistryCorruptionError, match="expected sessions mapping"):
        SessionRegistry(storage_dir=tmp_path)

    assert registry_file.read_bytes() == corrupted


def test_wrong_session_record_shape_raises_and_preserves_bytes(tmp_path: Path):
    corrupted = b'{"active_id": "sess_bad", "sessions": {"sess_bad": {"turns": "wrong"}}}'
    registry_file = tmp_path / "tool_sessions.json"
    registry_file.write_bytes(corrupted)

    with pytest.raises(SessionRegistryCorruptionError, match="invalid session record"):
        SessionRegistry(storage_dir=tmp_path)

    assert registry_file.read_bytes() == corrupted


def test_wrong_typed_turns_with_valid_local_id_raises_and_preserves_bytes(tmp_path: Path):
    corrupted = b'{"active_id": "sess_bad", "sessions": {"sess_bad": {"local_id": "sess_bad", "turns": "wrong"}}}'
    registry_file = tmp_path / "tool_sessions.json"
    registry_file.write_bytes(corrupted)

    with pytest.raises(SessionRegistryCorruptionError, match="invalid session record"):
        SessionRegistry(storage_dir=tmp_path)

    assert registry_file.read_bytes() == corrupted


def test_wrong_active_id_type_raises_and_preserves_bytes(tmp_path: Path):
    corrupted = b'{"active_id": [], "sessions": {}}'
    registry_file = tmp_path / "tool_sessions.json"
    registry_file.write_bytes(corrupted)

    with pytest.raises(SessionRegistryCorruptionError, match="invalid active session"):
        SessionRegistry(storage_dir=tmp_path)

    assert registry_file.read_bytes() == corrupted


def test_delete_session_removes_only_local_logs(tmp_path: Path):
    from gpt_cli.session.logger import SessionLogManager

    logs = tmp_path / "logs"
    manager = SessionLogManager(logs)
    manager.log_turn("sess_keep", 1, "keep", "keep", "model", 1)
    manager.log_turn("sess_delete", 1, "delete", "delete", "model", 1)

    reg = SessionRegistry(storage_dir=tmp_path / "registry")
    reg.create_session(local_id="sess_delete")
    reg.create_session(local_id="sess_keep")

    assert reg.delete_session("sess_delete") is True
    assert manager.get_history("sess_keep")
    assert manager.get_history("sess_delete")

    assert manager.delete_session_logs("sess_delete") is True
    assert manager.get_history("sess_keep")
    assert manager.get_history("sess_delete") == []


def test_session_log_delete_allows_only_single_safe_component(tmp_path: Path):
    from gpt_cli.session.logger import SessionLogManager
    manager = SessionLogManager(tmp_path / "logs")
    for bad in [".", "./", "nested/..", "a/b", ".."]:
        try:
            manager.session_dir(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe id accepted: {bad}")


def test_second_delete_is_not_success(tmp_path: Path):
    from gpt_cli.session.logger import SessionLogManager
    manager = SessionLogManager(tmp_path / "logs")
    manager.log_turn("sess_one", 1, "x", "x", "m", 1)
    assert manager.delete_session_logs("sess_one") is True
    assert manager.delete_session_logs("sess_one") is False
