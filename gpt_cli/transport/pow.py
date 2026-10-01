import base64
import calendar
import hashlib
import json
import random
import time
from typing import Any

from ..config import defaults

BOOTSTRAP_PROOF_PREFIX = "gAAAAAC"
PROOF_TOKEN_PREFIX = "gAAAAAB"
_BOOTSTRAP_PROOF_TTL_SECONDS = 600.0
SENTINEL_PROOF_MAX_ATTEMPTS = 500_000
_BOOTSTRAP_DIFFICULTY = "0"
_SENTINEL_BUILD_HASH = f"prod-{defaults.DEFAULT_BUILD_HASH}"
_WEBKIT_GUM_STRING = (
    "webkitGetUserMedia−"  # U+2212 MINUS SIGN is load-bearing
    "function webkitGetUserMedia() { [native code] }"
)
_BOOTSTRAP_POW_FALLBACK_MARKER = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"

_DATE_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_DATE_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

_bootstrap_cache: tuple[str, str, str, float] | None = None

class SentinelPowExhausted(RuntimeError):
    """The SHA3-512 proof search ran out of its attempt budget."""

def sentinel_date_str(timestamp: float) -> str:
    """JS-style local date string for fingerprint element [1]."""
    local = time.localtime(timestamp)
    offset_seconds = calendar.timegm(local) - int(timestamp)
    sign = "+" if offset_seconds >= 0 else "-"
    magnitude = abs(offset_seconds)
    hours, remainder = divmod(magnitude // 60, 60)
    minutes = remainder % 60
    return (
        f"{_DATE_DAYS[local.tm_wday]} {_DATE_MONTHS[local.tm_mon - 1]} "
        f"{local.tm_mday:02d} {local.tm_year} "
        f"{local.tm_hour:02d}:{local.tm_min:02d}:{local.tm_sec:02d} "
        f"GMT{sign}{hours:02d}{minutes:02d}"
    )

def sentinel_fingerprint(
    user_agent: str,
    device_id: str,
    timestamp: float,
    attempt: int = 0,
    elapsed_ms: float = 0.0,
) -> list[Any]:
    """Build the verified fixed-order 18-element sentinel config array."""
    return [
        4000,
        sentinel_date_str(timestamp),
        4294705152,
        attempt,
        user_agent,
        None,
        _SENTINEL_BUILD_HASH,
        "en-US",
        "en-US",
        elapsed_ms,
        _WEBKIT_GUM_STRING,
        "location",
        "ontransitionend",
        123.456,
        device_id,
        "",
        8,
        float(int(timestamp * 1000)),
    ]

def encode_sentinel_config(config: list[Any]) -> str:
    """Compact-JSON + base64 encode one config array, Go byte-shape."""
    normalized = [
        int(value) if isinstance(value, float) and value.is_integer() else value
        for value in config
    ]
    text = json.dumps(normalized, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(text.encode("utf-8")).decode("ascii")

def solve_sentinel_pow(
    seed: str,
    difficulty: str,
    user_agent: str,
    device_id: str,
    *,
    max_attempts: int = SENTINEL_PROOF_MAX_ATTEMPTS,
) -> str:
    """Solve the SHA3-512 prefix proof; returns bare base64 (NO prefix)."""
    target = difficulty or ""
    if not target:
        raise SentinelPowExhausted("Empty proof difficulty never matches.")
    started = time.monotonic()
    timestamp = time.time()
    config = sentinel_fingerprint(user_agent, device_id, timestamp)
    target_bytes = target.encode("ascii")
    diff_len = len(target)

    for attempt in range(max_attempts):
        config[3] = attempt
        config[9] = (time.monotonic() - started) * 1000.0
        encoded = encode_sentinel_config(config)
        digest = hashlib.sha3_512((seed + encoded).encode("utf-8")).hexdigest().encode("ascii")
        if digest[:diff_len] <= target_bytes:
            return encoded

    raise SentinelPowExhausted(f"No proof found within {max_attempts} attempts.")

def bootstrap_pow_fallback(seed: str) -> str:
    return _BOOTSTRAP_POW_FALLBACK_MARKER + base64.b64encode(
        json.dumps(seed).encode("utf-8")
    ).decode("ascii")

def bootstrap_proof_token(user_agent: str, device_id: str) -> str:
    """Self-solve the sentinel bootstrap proof locally (cached for 10 minutes)."""
    global _bootstrap_cache
    now = time.monotonic()
    if _bootstrap_cache is not None:
        proof, cached_ua, cached_device, expire_at = _bootstrap_cache
        if now < expire_at and cached_ua == user_agent and cached_device == device_id:
            return proof

    seed = format(random.random(), ".6f")
    try:
        answer = solve_sentinel_pow(seed, _BOOTSTRAP_DIFFICULTY, user_agent, device_id)
    except SentinelPowExhausted:
        answer = bootstrap_pow_fallback(seed)
    proof = BOOTSTRAP_PROOF_PREFIX + answer
    _bootstrap_cache = (
        proof,
        user_agent,
        device_id,
        now + _BOOTSTRAP_PROOF_TTL_SECONDS,
    )
    return proof
