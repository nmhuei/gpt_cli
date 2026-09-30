import time
import pytest
from gpt_cli.transport.pow import solve_sentinel_pow, bootstrap_proof_token, PROOF_TOKEN_PREFIX, BOOTSTRAP_PROOF_PREFIX

def test_solve_pow_known_vector():
    seed = "0.123456789"
    difficulty = "005"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36"
    dev_id = "test-device-uuid"

    t0 = time.perf_counter()
    result = solve_sentinel_pow(seed, difficulty, ua, dev_id)
    t1 = time.perf_counter()

    assert isinstance(result, str)
    assert len(result) > 50
    assert (t1 - t0) < 1.0  # Must solve under 1 second easily

def test_bootstrap_proof_token():
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36"
    dev_id = "test-device-uuid"

    token = bootstrap_proof_token(ua, dev_id)
    assert token.startswith(BOOTSTRAP_PROOF_PREFIX)
    assert len(token) > 50
