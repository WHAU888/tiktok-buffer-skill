#!/usr/bin/env python3
"""Report what is set up for the tiktok-buffer skill. Never prints keys.

  status.py                 quick local check (no network)
  status.py --online        also checks the Buffer key works and lists TikTok channels
  status.py --mark-briefed  record that the user has seen the first-use briefing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffer  # noqa: E402
import storage  # noqa: E402

BRIEFED_FILE = storage.CONFIG_DIR / ".briefed"
PENDING_FILE = storage.CONFIG_DIR / "pending.json"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--online", action="store_true")
    p.add_argument("--mark-briefed", action="store_true")
    a = p.parse_args()

    if a.mark_briefed:
        storage.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        BRIEFED_FILE.write_text(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n")
        print(json.dumps({"briefed": True}))
        return

    has_key = bool(os.environ.get("BUFFER_API_KEY", "").strip()) or (
        buffer.KEY_FILE.exists() and bool(buffer.KEY_FILE.read_text().strip()))
    cfg = storage.load_config()
    locs = cfg.get("locations") or {}
    pending = json.loads(PENDING_FILE.read_text()) if PENDING_FILE.exists() else []

    report: dict = {
        "briefed": BRIEFED_FILE.exists(),
        "bufferKeySaved": has_key,
        "storageLocations": [
            {"name": n, "default": n == cfg.get("default"), "linkType": l.get("link_mode", "public"),
             "keysSaved": storage.key_file(n).exists()}
            for n, l in locs.items()
        ],
        "clipsWaitingOnStorage": len(pending),
    }

    if a.online and has_key:
        try:
            orgs = buffer.gql("query { account { organizations { id name } } }")["account"]["organizations"]
            chans = []
            for org in orgs:
                for c in buffer.gql(
                    "query($o: OrganizationId!) { channels(input:{organizationId:$o}) "
                    "{ id name service isDisconnected timezone } }", {"o": org["id"]})["channels"]:
                    if "tiktok" in str(c.get("service", "")).lower():
                        chans.append({**c, "organizationId": org["id"]})
            report["bufferConnection"] = "ok"
            report["tiktokChannels"] = chans
        except buffer.ApiError as e:
            report["bufferConnection"] = f"failed: {e}"

    missing = []
    if not has_key:
        missing.append("buffer_key")
    elif report.get("bufferConnection", "ok") != "ok":
        missing.append("buffer_key_invalid")
    elif a.online and not report.get("tiktokChannels"):
        missing.append("tiktok_not_connected_in_buffer")
    elif a.online and any(c.get("isDisconnected") for c in report["tiktokChannels"]):
        missing.append("tiktok_disconnected")
    if not locs:
        missing.append("storage_location (optional: only for posting files from this computer)")
    report["missing"] = missing
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
