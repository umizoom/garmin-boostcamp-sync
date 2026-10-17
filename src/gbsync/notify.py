"""Discord webhook notifications for things that need a person's attention."""

from __future__ import annotations

import json
import logging
import urllib.request

from .config import Config

log = logging.getLogger(__name__)


def send(cfg: Config, text: str) -> None:
    """Post `text` to the Discord webhook, if one is configured. Never raises,
    so a notification problem can't break a sync."""
    if not cfg.discord_webhook:
        return
    req = urllib.request.Request(
        cfg.discord_webhook,
        data=json.dumps({"content": f"gbsync: {text}"}).encode(),
        # Discord rejects urllib's default User-Agent.
        headers={"Content-Type": "application/json", "User-Agent": "gbsync"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log.warning("Discord notification failed: %s", e)
