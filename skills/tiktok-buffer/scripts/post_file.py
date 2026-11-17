#!/usr/bin/env python3
"""Post a local video or photo to TikTok via Buffer, using a storage location for the link.

Commands:
  post FILE --channel ID (--text ... | --text-file ...) [schedule options] [--location NAME] [--yes]
      Without --yes: checks the file, picks the location and prints one preview
      covering upload + post. Nothing is uploaded or sent.
      With --yes: upload (random name) -> check link -> create Buffer post.
      If any step after the upload fails, the uploaded file is deleted.
  cleanup [--dry-run]
      Delete uploaded files whose Buffer post has been sent (or was deleted).
  pending
      List uploaded files still waiting for their post.

Pending uploads are tracked in ~/.config/tiktok-buffer/pending.json.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffer  # noqa: E402
import storage  # noqa: E402

PENDING_FILE = storage.CONFIG_DIR / "pending.json"
VIDEO_EXTS = {".mp4", ".mov", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
PLACEHOLDER_URL = "https://upload.pending/clip"


def load_pending() -> list:
    return json.loads(PENDING_FILE.read_text()) if PENDING_FILE.exists() else []


def save_pending(items: list) -> None:
    storage.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps(items, indent=2) + "\n")


def check_media(path: Path) -> dict:
    """Validate a local video or photo. Returns info including kind: video | image."""
    if not path.is_file():
        buffer.fail(f"file not found: {path}")
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        kind = "video"
    elif ext in IMAGE_EXTS:
        kind = "image"
    else:
        buffer.fail(f"{path.name} is not a file TikTok accepts "
                    "(videos: .mp4, .mov, .webm; photos: .jpg, .jpeg, .png, .webp)")
    size = path.stat().st_size
    if size == 0:
        buffer.fail(f"{path.name} is empty")
    info = {"file": str(path), "kind": kind, "sizeMB": round(size / 1_048_576, 1)}
    if kind == "video" and shutil.which("ffprobe"):
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=30)
            info["durationSec"] = round(float(r.stdout.strip()), 1)
        except (ValueError, subprocess.SubprocessError):
            pass
    return info


def presign_expiry(args) -> int | None:
    """Seconds the link must live: until 24 h after the scheduled time, max 7 days."""
    if not args.at or args.draft:
        buffer.fail("this location uses expiring links, which need a fixed time: use --at (not --queue/--draft)")
    due = datetime.strptime(buffer.to_utc_iso(args.at), "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc)
    seconds = int((due - datetime.now(timezone.utc)).total_seconds()) + 24 * 3600
    if seconds > storage.MAX_PRESIGN_SECONDS:
        buffer.fail("expiring links last at most 7 days: schedule within 6 days, or use a public-link location")
    return seconds


def cmd_post(args) -> None:
    src = Path(args.file).expanduser().resolve()
    media = check_media(src)
    kind = media["kind"]
    try:
        loc_name, loc = storage.choose_location(args.location)
    except storage.StorageError as e:
        buffer.fail(f"[{e.code}] {e}")
    mode = loc.get("link_mode", "public")
    expires = presign_expiry(args) if mode == "presigned" else None

    # Validate the post itself before touching storage.
    if kind == "video":
        args.video_url, args.image_url = PLACEHOLDER_URL, None
    else:
        args.video_url, args.image_url = None, [PLACEHOLDER_URL]
    inp = buffer.build_create_input(args)

    plan = {
        "media": media,
        "upload": {
            "location": loc_name, "bucket": loc.get("bucket"), "linkType": mode,
            "fileName": "random, hard to guess",
            "exposure": ("public link (anyone with it can watch) until the post is sent, then deleted"
                         if mode == "public" else
                         f"expiring link (~{round(expires / 3600)} h), deleted after the post is sent"),
        },
        "post": {k: v for k, v in inp.items() if k != "assets"},
    }
    if not args.yes:
        print(json.dumps({"preview": True, "note": "Nothing uploaded or sent. Re-run with --yes after the user approves.",
                          **plan}, indent=2, ensure_ascii=False))
        return

    try:
        r2 = storage.R2(loc_name, loc)
        key = storage.new_key(loc, src.name)
        r2.upload(src, key)
    except storage.StorageError as e:
        buffer.fail(f"upload failed, nothing scheduled: {e}")

    def undo(reason: str) -> None:
        reason = reason.rstrip(". ") + "."
        try:
            r2.delete(key)
            buffer.fail(f"{reason} The uploaded file was deleted; nothing was scheduled.")
        except storage.StorageError as e2:
            buffer.fail(f"{reason} ALSO could not delete the uploaded file ({e2}); "
                        f"remove {loc['bucket']}/{key} in the Cloudflare dashboard.")

    url = r2.link(key, expires=expires)
    try:
        ctype = storage.check_link(url)
    except storage.StorageError as e:
        undo(f"{e}.")
    if ctype and not ctype.startswith(f"{kind}/"):
        undo(f"the link opens but serves {ctype!r}, not a {'video' if kind == 'video' else 'photo'}.")

    inp["assets"][0][kind]["url"] = url
    try:
        post = buffer.create_post(inp)
    except buffer.ApiError as e:
        undo(f"{e}.")

    items = load_pending()
    items.append({"postId": post["id"], "location": loc_name, "key": key, "dueAt": post.get("dueAt"),
                  "uploadedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    save_pending(items)
    print(json.dumps({"post": post, "upload": {"location": loc_name, "linkType": mode},
                      "cleanup": "file will be deleted after the post is sent (run `cleanup`)"}, indent=2,
                     ensure_ascii=False))


def cmd_cleanup(args) -> None:
    items = load_pending()
    keep, report = [], []
    cfg = storage.load_config().get("locations", {})
    for it in items:
        try:
            status = buffer.post_status(it["postId"])
        except buffer.ApiError as e:
            keep.append(it); report.append({**it, "action": "kept", "why": str(e)}); continue
        if status in ("sent", None):
            why = "post sent" if status == "sent" else "post no longer in Buffer"
            if args.dry_run:
                keep.append(it); report.append({**it, "action": "would delete", "why": why}); continue
            loc = cfg.get(it["location"])
            try:
                if not loc:
                    raise storage.StorageError(f"location {it['location']!r} no longer set up")
                storage.R2(it["location"], loc).delete(it["key"])
                report.append({**it, "action": "deleted", "why": why})
            except storage.StorageError as e:
                keep.append(it); report.append({**it, "action": "kept", "why": f"delete failed: {e}"})
        else:
            keep.append(it)
            report.append({**it, "action": "kept", "why": f"post status is {status!r}"})
    if not args.dry_run:
        save_pending(keep)
    print(json.dumps({"checked": len(items), "results": report}, indent=2))


def cmd_pending(_a) -> None:
    print(json.dumps(load_pending(), indent=2))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("post", help="upload a local video or photo and create the Buffer post")
    c.add_argument("file")
    c.add_argument("--channel", required=True)
    txt = c.add_mutually_exclusive_group(required=True)
    txt.add_argument("--text")
    txt.add_argument("--text-file")
    c.add_argument("--at")
    c.add_argument("--queue", action="store_true")
    c.add_argument("--now", action="store_true")
    c.add_argument("--draft", action="store_true")
    c.add_argument("--reminder", action="store_true")
    c.add_argument("--ai-generated", action="store_true")
    c.add_argument("--thumbnail-offset-ms", type=int, help="video only: cover frame in ms")
    c.add_argument("--title", help="photo only: title for the TikTok photo post")
    c.add_argument("--location", help="storage location name (default rules apply if omitted)")
    c.add_argument("--yes", action="store_true", help="upload and send (only after user approval)")
    c.set_defaults(fn=cmd_post)
    cl = sub.add_parser("cleanup"); cl.add_argument("--dry-run", action="store_true"); cl.set_defaults(fn=cmd_cleanup)
    sub.add_parser("pending").set_defaults(fn=cmd_pending)
    a = p.parse_args()
    try:
        a.fn(a)
    except (buffer.ApiError, storage.StorageError) as e:
        buffer.fail(str(e))


if __name__ == "__main__":
    main()
