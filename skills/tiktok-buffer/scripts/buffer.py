#!/usr/bin/env python3
"""Small Buffer GraphQL client for TikTok posting and analytics.

Standard library only. The API key comes from the BUFFER_API_KEY environment
variable, or from ~/.config/tiktok-buffer/api_key. The key is never printed.

Commands:
  channels                      List organizations and their TikTok channels.
  create ...                    Build a TikTok post. Prints a preview only,
                                unless --yes is given.
  posts --org ID [--channel ID] List recent posts with metrics.
  post ID                       One post with metrics.

Run `buffer.py <command> -h` for options.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://api.buffer.com"
KEY_FILE = Path.home() / ".config" / "tiktok-buffer" / "api_key"
TIKTOK_VIDEO_CAPTION_LIMIT = 2200
TIKTOK_OTHER_CAPTION_LIMIT = 4000


class ApiError(Exception):
    """A Buffer request failed. The message is safe to show the user."""


def fail(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def load_key() -> str:
    key = os.environ.get("BUFFER_API_KEY", "").strip()
    if key:
        return key
    if KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
        if key:
            return key
    fail(
        "no Buffer API key. Set BUFFER_API_KEY or save the key to "
        f"{KEY_FILE} (chmod 600). Create one at https://publish.buffer.com/settings/api"
    )
    return ""  # unreachable


def gql(query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {load_key()}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ApiError("Buffer rejected the API key (401). Check or regenerate it.")
        if e.code == 429:
            wait = e.headers.get("Retry-After", "?")
            raise ApiError(f"rate limited by Buffer (429). Retry after {wait} seconds.")
        raise ApiError(f"HTTP {e.code} from Buffer: {e.read().decode(errors='replace')[:500]}")
    except urllib.error.URLError as e:
        raise ApiError(f"could not reach Buffer: {e.reason}")
    if payload.get("errors"):
        msgs = "; ".join(
            f"{err.get('message')} ({err.get('extensions', {}).get('code', '')})"
            for err in payload["errors"]
        )
        raise ApiError(f"Buffer API error: {msgs}")
    return payload.get("data") or {}


def create_post(inp: dict) -> dict:
    """Send createPost. Returns the post dict or raises ApiError."""
    data = gql(
        """mutation($input: CreatePostInput!) {
          createPost(input: $input) {
            ... on PostActionSuccess { post { id text dueAt status channelId } }
            ... on MutationError { message }
          }
        }""",
        {"input": inp},
    )
    res = data.get("createPost") or {}
    if "post" not in res:
        raise ApiError(f"Buffer did not create the post: {res.get('message', 'no reason given')}")
    return res["post"]


def post_status(post_id: str) -> str | None:
    """Return the post's status (e.g. sent, error), or None if Buffer can't find it."""
    try:
        data = gql('query($id: PostId!) { post(input: { id: $id }) { status } }', {"id": post_id})
    except ApiError as e:
        if "NOT_FOUND" in str(e):
            return None
        raise
    post = data.get("post")
    return post.get("status") if post else None


def out(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


# ---------- channels ----------

def cmd_channels(_args) -> None:
    orgs = gql("query { account { organizations { id name } } }")["account"]["organizations"]
    result = []
    for org in orgs:
        chans = gql(
            """query($org: OrganizationId!) {
              channels(input: { organizationId: $org }) {
                id name displayName service isQueuePaused isDisconnected timezone
              }
            }""",
            {"org": org["id"]},
        )["channels"]
        tiktok = [c for c in chans if "tiktok" in str(c.get("service", "")).lower()]
        result.append({"organization": org, "tiktokChannels": tiktok,
                       "otherChannelCount": len(chans) - len(tiktok)})
    out(result)


# ---------- create ----------

def to_utc_iso(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        fail(f"--at must be ISO 8601 with a timezone, e.g. 2026-11-20T19:00:00+08:00 (got {value!r})")
    if dt.tzinfo is None:
        fail("--at needs a timezone offset (e.g. +08:00 or Z) so the post goes out at the right time")
    dt = dt.astimezone(timezone.utc)
    if dt <= datetime.now(timezone.utc):
        fail("--at is in the past")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_create_input(args) -> dict:
    if not args.video_url and not args.image_url:
        fail("TikTok posts need media: pass --video-url, or one or more --image-url for a photo post")
    if args.video_url and args.image_url:
        fail("use either --video-url or --image-url, not both")
    for url in [args.video_url, *(args.image_url or [])]:
        if url and not url.startswith("https://"):
            fail(f"media URL must be a public https:// link Buffer can fetch: {url}")

    text = args.text if args.text is not None else Path(args.text_file).read_text()
    limit = TIKTOK_VIDEO_CAPTION_LIMIT if args.video_url else TIKTOK_OTHER_CAPTION_LIMIT
    if len(text) > limit:
        fail(f"caption is {len(text)} characters; TikTok via Buffer allows {limit} for this post type")

    modes = [bool(args.at), args.queue, args.now]
    if sum(modes) > 1:
        fail("choose only one of --at, --queue, --now")
    if args.draft and args.now:
        fail("--draft and --now conflict")

    inp: dict = {
        "channelId": args.channel,
        "text": text,
        "schedulingType": "notification" if args.reminder else "automatic",
    }
    if args.at:
        inp["mode"] = "customScheduled"
        inp["dueAt"] = to_utc_iso(args.at)
    elif args.now:
        inp["mode"] = "shareNow"
    else:
        inp["mode"] = "addToQueue"
    if args.draft:
        inp["saveToDraft"] = True

    if args.video_url:
        video: dict = {"url": args.video_url}
        if args.thumbnail_offset_ms is not None:
            video["metadata"] = {"thumbnailOffset": args.thumbnail_offset_ms}
        inp["assets"] = [{"video": video}]
    else:
        inp["assets"] = [{"image": {"url": u}} for u in args.image_url]

    tiktok_meta: dict = {}
    if args.ai_generated:
        tiktok_meta["isAiGenerated"] = True
    if args.title:
        tiktok_meta["title"] = args.title
    if tiktok_meta:
        inp["metadata"] = {"tiktok": tiktok_meta}
    return inp


def cmd_create(args) -> None:
    inp = build_create_input(args)
    if not args.yes:
        out({"preview": True, "note": "Nothing sent. Re-run with --yes after the user approves.",
             "input": inp, "captionLength": len(inp["text"])})
        return
    out({"post": create_post(inp)})


# ---------- analytics ----------

METRICS = "metrics { type name value unit } metricsUpdatedAt"


def cmd_posts(args) -> None:
    flt: dict = {"status": [args.status]}
    if args.channel:
        flt["channelIds"] = [args.channel]
    data = gql(
        f"""query($first: Int!, $input: PostsInput!) {{
          posts(first: $first, input: $input) {{
            edges {{ node {{ id text dueAt channelId {METRICS} }} }}
            pageInfo {{ hasNextPage endCursor }}
          }}
        }}""",
        {"first": args.limit, "input": {"organizationId": args.org, "filter": flt}},
    )
    out([e["node"] for e in data["posts"]["edges"]])


def cmd_post(args) -> None:
    data = gql(
        f"""query($id: PostId!) {{
          post(input: {{ id: $id }}) {{ id text dueAt channelId {METRICS} }}
        }}""",
        {"id": args.id},
    )
    out(data.get("post"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("channels", help="list organizations and TikTok channels").set_defaults(fn=cmd_channels)

    c = sub.add_parser("create", help="create a TikTok post (preview unless --yes)")
    c.add_argument("--channel", required=True, help="Buffer channel id of the TikTok account")
    txt = c.add_mutually_exclusive_group(required=True)
    txt.add_argument("--text", help="caption including hashtags")
    txt.add_argument("--text-file", help="read caption from a file (safer for long captions)")
    c.add_argument("--video-url", help="public https URL of the video")
    c.add_argument("--image-url", action="append", help="public https URL of an image (repeat for a carousel)")
    c.add_argument("--title", help="title for photo posts")
    c.add_argument("--at", help="schedule time, ISO 8601 with offset, e.g. 2026-11-20T19:00:00+08:00")
    c.add_argument("--queue", action="store_true", help="add to the channel's Buffer queue (default)")
    c.add_argument("--now", action="store_true", help="share immediately")
    c.add_argument("--draft", action="store_true", help="save as a Buffer draft; not published until scheduled")
    c.add_argument("--reminder", action="store_true",
                   help="notification publishing: Buffer reminds the user to post by hand")
    c.add_argument("--ai-generated", action="store_true", help="disclose AI-generated content (video)")
    c.add_argument("--thumbnail-offset-ms", type=int, help="video frame to use as cover, in ms")
    c.add_argument("--yes", action="store_true", help="actually send to Buffer (only after user approval)")
    c.set_defaults(fn=cmd_create)

    ps = sub.add_parser("posts", help="list posts with metrics")
    ps.add_argument("--org", required=True, help="organization id")
    ps.add_argument("--channel", help="channel id to filter")
    ps.add_argument("--status", default="sent", help="post status filter (default: sent)")
    ps.add_argument("--limit", type=int, default=20)
    ps.set_defaults(fn=cmd_posts)

    one = sub.add_parser("post", help="one post with metrics")
    one.add_argument("id")
    one.set_defaults(fn=cmd_post)

    args = p.parse_args()
    try:
        args.fn(args)
    except ApiError as e:
        fail(str(e))


if __name__ == "__main__":
    main()
