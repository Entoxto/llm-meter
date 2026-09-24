"""Validate the small image payload contract shared by chat transports."""
from __future__ import annotations

import base64
import binascii


MAX_IMAGE_BYTES = 20 * 1024 * 1024


def image_mime(encoded: str) -> str:
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("Chat image must be nonempty base64 text.")
    if len(encoded) > (MAX_IMAGE_BYTES * 4 // 3 + 8):
        raise ValueError("Chat image exceeds 20 MB.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Chat image is not valid base64.") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Chat image exceeds 20 MB.")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise ValueError("Chat image must be PNG or JPEG.")
