import re
from typing import Optional


def sanitize_phone(raw_phone: Optional[str]) -> str:
    """
    Normalizes arbitrary phone string into strict E.164 standard with + prefix.
    Supports Mexican 10-digit format by prepending mobile country code (+521).
    """
    if not raw_phone:
        return ""

    digits = re.sub(r"\D", "", str(raw_phone))
    if not digits:
        return ""

    # Enforce modern mobile prefix standard (+521 for MX)
    if len(digits) == 10:
        digits = f"521{digits}"
    elif len(digits) == 12 and digits.startswith("52") and not digits.startswith("521"):
        digits = f"521{digits[2:]}"

    return f"+{digits}"


def resolve_media_path(media_path: Optional[str], shared_media_dir: str) -> Optional[str]:
    """
    Validates and remaps media paths within the authorized shared mount directory.
    Returns None if the path is outside the authorized directory.
    """
    if not media_path:
        return None

    shared_media_dir = shared_media_dir.rstrip("/")
    if media_path.startswith("/app/shared_media/"):
        media_path = media_path.replace("/app/shared_media", shared_media_dir, 1)

    if not media_path.startswith(shared_media_dir):
        return None

    return media_path


def strip_markdown_media(message: str, shared_media_dir: str) -> str:
    """
    Strips raw markdown image tags referencing the shared media mount to
    prevent cluttering plain-text chat clients.
    """
    if not message:
        return ""

    shared_media_dir = shared_media_dir.rstrip("/")
    pattern = rf"\s*!\[.*?\]\({re.escape(shared_media_dir)}/[^\)]+\)\s*"
    return re.sub(pattern, "", message).strip()


def detect_ux_leak(message: str, shared_media_dir: str) -> bool:
    """
    Intercepts unintended internal system tags, temporary paths, or raw local file links
    hallucinated by LLM agents. Allows standard http(s) URLs and legitimate shared media mounts.
    """
    if not message:
        return False

    shared_media_dir = shared_media_dir.rstrip("/")
    leak_pattern = rf"(<media>|/tmp/|!\[.*?\]\((?!https?://|{re.escape(shared_media_dir)}/)[^\)]+\))"
    return bool(re.search(leak_pattern, message))
