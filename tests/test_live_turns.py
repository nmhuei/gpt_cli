import json
from pathlib import Path

import pytest

from gpt_cli.session.live_turns import (
    LiveTurnRegistry,
    SessionBusyError,
    TurnOwner,
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
