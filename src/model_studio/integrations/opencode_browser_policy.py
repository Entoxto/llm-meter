"""Pure edits for Studio-owned OpenCode browser permission leases.

The private OpenCode config denies browser actions by default. A confirmed
desktop attachment adds one session rule; a later user rule retains precedence.
The caller must send the returned permissions and metadata in one session update.
"""

from __future__ import annotations

from copy import deepcopy


MARKER = "model_studio_browser_host"
_ALLOW = {"action": "browser", "resource": "*", "effect": "allow"}


def _parts(session: dict) -> tuple[list[dict], dict]:
    return deepcopy(session.get("permissions") or []), deepcopy(session.get("metadata") or {})


def grant_browser(session: dict, owner_id: str) -> dict:
    """Return a session.update payload after a matching host attachment."""
    if not owner_id:
        raise ValueError("Browser host ownership ID is required")
    rules, metadata = _parts(session)
    if metadata.get(MARKER) == owner_id and rules and rules[0] == _ALLOW:
        return {"permissions": rules, "metadata": metadata}
    if metadata.get(MARKER) and rules and rules[0] == _ALLOW:
        rules.pop(0)
    metadata[MARKER] = owner_id
    return {"permissions": [dict(_ALLOW), *rules], "metadata": metadata}


def revoke_browser(session: dict, owner_id: str | None = None) -> dict | None:
    """Remove only the Studio rule for this owner; None clears stale launches."""
    rules, metadata = _parts(session)
    recorded = metadata.get(MARKER)
    if not recorded or (owner_id is not None and recorded != owner_id):
        return None
    if rules and rules[0] == _ALLOW:
        rules.pop(0)
    metadata.pop(MARKER, None)
    return {"permissions": rules, "metadata": metadata}
