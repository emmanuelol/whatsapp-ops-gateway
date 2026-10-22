import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

DEFAULT_IDEMPOTENCY_WINDOW_S = 600


def compute_idempotency_key(
    phone: str,
    message: str,
    media_path: Optional[str] = None,
    window_seconds: int = DEFAULT_IDEMPOTENCY_WINDOW_S,
    now: Optional[datetime] = None,
) -> str:
    """
    Computes a deterministic, time-quantized idempotency key.

    Re-sends of identical payload (phone, content, media) within the same
    window collapse to one key at the gateway. Legitimate periodic re-sends
    (e.g., keepalive, automated digests) rotate into a new bucket once the
    window expires.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    timestamp = int(now.timestamp())
    window = timestamp // window_seconds

    content_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
    media_str = media_path or ""
    hash_input = f"{phone}:{content_hash}:{media_str}:{window}"
    return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()


def compute_interactive_key(
    phone: str,
    prefix: str = "interactive",
    date_str: Optional[str] = None,
    entropy: Optional[str] = None,
) -> str:
    """
    Computes an entropy-backed idempotency key for interactive user requests.

    Allows distinct conversational replies on the same day without colliding
    with daily automated reports.
    """
    if date_str is None:
        date_str = datetime.now(timezone.utc).date().isoformat()
    if entropy is None:
        entropy = uuid.uuid4().hex

    hash_input = f"{prefix}:{phone}:{date_str}:{entropy}"
    return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
