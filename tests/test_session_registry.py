import pytest
from pathlib import Path
from gpt_cli.session.models import SessionRecord
from gpt_cli.session.registry import SessionRegistry

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
