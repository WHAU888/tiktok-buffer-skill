#!/usr/bin/env python3
"""Storage locations for clips that Buffer must fetch by URL.

Standard library only. Supports Cloudflare R2 (S3-compatible API, SigV4).

Settings live in ~/.config/tiktok-buffer/:
  storage.json          list of locations and the default (no secrets)
  r2-<name>.json        that location's access keys (chmod 600, never printed)

Commands:
  setup                 Interactive: add or replace a location, then self-test.
                        The user runs this in their own terminal (keys are typed
                        with hidden input, never shown in chat).
  list                  Show locations and which one is the default.
  default NAME          Make NAME the default location.
  test [--location N]   Upload a tiny file, check its link, delete it.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "tiktok-buffer"
STORAGE_FILE = CONFIG_DIR / "storage.json"
MAX_PRESIGN_SECONDS = 7 * 24 * 3600  # SigV4 hard limit


class StorageError(Exception):
    """Message is safe to show the user. `code` tells the skill what to do next."""

    def __init__(self, msg: str, code: str = "ERROR"):
        super().__init__(msg)
        self.code = code


# ---------- config ----------

def load_config() -> dict:
    if not STORAGE_FILE.exists():
        return {"default": None, "locations": {}}
    return json.loads(STORAGE_FILE.read_text())


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_FILE.write_text(json.dumps(cfg, indent=2) + "\n")


def key_file(name: str) -> Path:
    return CONFIG_DIR / f"r2-{name}.json"


def choose_location(name: str | None = None) -> tuple[str, dict]:
    """Pick a location by the rules: named > default > only one. Never guesses between several."""
    cfg = load_config()
    locs = cfg.get("locations") or {}
    if not locs:
        raise StorageError(
            "No storage location is set up. Run `storage.py setup` in a terminal, "
            "give a public video link instead, or post via the TikTok app / Buffer website.",
            "NO_LOCATION",
        )
    if name:
        if name not in locs:
            raise StorageError(f"No location called {name!r}. Set up: {', '.join(sorted(locs))}", "UNKNOWN_LOCATION")
        return name, locs[name]
    default = cfg.get("default")
    if default and default in locs:
        return default, locs[default]
    if len(locs) == 1:
        only = next(iter(locs))
        return only, locs[only]
    raise StorageError(
        f"Several locations are set up and none is the default: {', '.join(sorted(locs))}. "
        "Ask the user which to use (and whether to make it the default).",
        "CHOOSE_LOCATION",
    )


# ---------- SigV4 ----------

def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str, service: str = "s3") -> bytes:
    k = _hmac(("AWS4" + secret).encode(), date)
    k = _hmac(k, region)
    k = _hmac(k, service)
    return _hmac(k, "aws4_request")


def _q(s: str) -> str:
    return urllib.parse.quote(s, safe="-_.~")


def presign_url(host: str, path: str, access_key: str, secret: str, region: str,
                expires: int, now: datetime | None = None, method: str = "GET") -> str:
    """Query-string-signed URL (AWS SigV4). `path` must already be URI-encoded."""
    now = now or datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = amz_date[:8]
    scope = f"{date}/{region}/s3/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "host",
    }
    cq = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(params.items()))
    creq = "\n".join([method, path, cq, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(creq.encode()).hexdigest()])
    sig = hmac.new(_signing_key(secret, date, region), sts.encode(), hashlib.sha256).hexdigest()
    return f"https://{host}{path}?{cq}&X-Amz-Signature={sig}"


class R2:
    def __init__(self, name: str, loc: dict):
        self.name = name
        self.loc = loc
        kf = key_file(name)
        if not kf.exists():
            raise StorageError(f"Keys for location {name!r} are missing ({kf}). Re-run `storage.py setup`.", "NO_KEYS")
        keys = json.loads(kf.read_text())
        self.access_key = keys["access_key_id"]
        self.secret = keys["secret_access_key"]
        self.host = f"{loc['account_id']}.r2.cloudflarestorage.com"
        self.bucket = loc["bucket"]
        self.region = "auto"

    def _path(self, key: str) -> str:
        return "/" + urllib.parse.quote(f"{self.bucket}/{key}", safe="/-_.~")

    def _request(self, method: str, key: str, body=None, length: int = 0,
                 payload_hash: str | None = None, content_type: str | None = None) -> int:
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = amz_date[:8]
        payload_hash = payload_hash or hashlib.sha256(b"").hexdigest()
        path = self._path(key)
        headers = {"host": self.host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
        signed = ";".join(sorted(headers))
        canon_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        creq = "\n".join([method, path, "", canon_headers, signed, payload_hash])
        scope = f"{date}/{self.region}/s3/aws4_request"
        sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(creq.encode()).hexdigest()])
        sig = hmac.new(_signing_key(self.secret, date, self.region), sts.encode(), hashlib.sha256).hexdigest()
        req_headers = {
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
                             f"SignedHeaders={signed}, Signature={sig}",
        }
        if body is not None:
            req_headers["Content-Length"] = str(length)
            if content_type:
                req_headers["Content-Type"] = content_type
        req = urllib.request.Request(f"https://{self.host}{path}", data=body, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 403:
                raise StorageError(f"R2 refused the request (403): check the keys and that the token can write to bucket {self.bucket!r}. {detail}")
            if e.code == 404 and method != "DELETE":
                raise StorageError(f"R2 bucket {self.bucket!r} not found (404). {detail}")
            raise StorageError(f"R2 error {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise StorageError(f"could not reach R2: {e.reason}")

    def upload(self, src: Path, key: str) -> None:
        h = hashlib.sha256()
        with src.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        ctype = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
        with src.open("rb") as f:
            self._request("PUT", key, body=f, length=src.stat().st_size,
                          payload_hash=h.hexdigest(), content_type=ctype)

    def delete(self, key: str) -> None:
        self._request("DELETE", key)

    def link(self, key: str, expires: int | None = None) -> str:
        if self.loc.get("link_mode", "public") == "presigned":
            if not expires:
                raise StorageError("presigned links need an expiry")
            return presign_url(self.host, self._path(key), self.access_key, self.secret,
                               self.region, min(expires, MAX_PRESIGN_SECONDS))
        base = self.loc["public_base_url"].rstrip("/")
        return f"{base}/{urllib.parse.quote(key, safe='/-_.~')}"


def new_key(loc: dict, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    prefix = (loc.get("prefix") or "clips/").lstrip("/")
    return f"{prefix}{secrets.token_urlsafe(18)}{ext}"


def check_link(url: str, tries: int = 4) -> str:
    """GET the first byte. Returns the content type, or raises StorageError."""
    last = ""
    for i in range(tries):
        req = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": "tiktok-buffer-skill"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status in (200, 206):
                    return resp.headers.get("Content-Type", "")
                last = f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except urllib.error.URLError as e:
            last = str(e.reason)
        time.sleep(2 * (i + 1))
    raise StorageError(f"the uploaded file's link does not open ({last}). Is public access turned on for the bucket?", "LINK_FAILED")


# ---------- commands ----------

def cmd_list(_a) -> None:
    cfg = load_config()
    rows = []
    for name, loc in (cfg.get("locations") or {}).items():
        rows.append({
            "name": name, "default": name == cfg.get("default"), "type": loc.get("type"),
            "bucket": loc.get("bucket"), "link_mode": loc.get("link_mode", "public"),
            "public_base_url": loc.get("public_base_url"), "keys_saved": key_file(name).exists(),
        })
    print(json.dumps(rows, indent=2))


def cmd_default(a) -> None:
    cfg = load_config()
    if a.name not in (cfg.get("locations") or {}):
        sys.exit(f"error: no location called {a.name!r}")
    cfg["default"] = a.name
    save_config(cfg)
    print(f"default location is now {a.name!r}")


def self_test(name: str, loc: dict) -> None:
    r2 = R2(name, loc)
    key = new_key(loc, "selftest.txt")
    tmp = CONFIG_DIR / ".selftest.txt"
    tmp.write_text("tiktok-buffer storage self-test\n")
    try:
        r2.upload(tmp, key)
        print("  upload ............ ok")
        check_link(r2.link(key, expires=600))
        print("  link opens ........ ok")
    finally:
        try:
            r2.delete(key)
            print("  delete ............ ok")
        finally:
            tmp.unlink(missing_ok=True)


def cmd_test(a) -> None:
    name, loc = choose_location(a.location)
    print(f"Testing location {name!r}...")
    self_test(name, loc)
    print("All good.")


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or (default or "")


def cmd_setup(_a) -> None:
    if not sys.stdin.isatty():
        sys.exit("error: setup is interactive. Run it yourself in a Terminal window.")
    cfg = load_config()
    print("Add a Cloudflare R2 storage location. Keys are typed hidden and saved only on this Mac.\n")
    name = ask("Name for this location", "personal")
    account_id = ask("Cloudflare account ID")
    bucket = ask("Bucket name", "tiktok-clips")
    mode = ask("Link type: 'public' (bucket public URL) or 'presigned' (expiring link, experimental with Buffer)", "public")
    if mode not in ("public", "presigned"):
        sys.exit("error: link type must be public or presigned")
    loc = {"type": "r2", "account_id": account_id, "bucket": bucket, "link_mode": mode, "prefix": "clips/"}
    if mode == "public":
        loc["public_base_url"] = ask("Public bucket URL (e.g. https://pub-xxxx.r2.dev)")
        if not loc["public_base_url"].startswith("https://"):
            sys.exit("error: public URL must start with https://")
    access = getpass.getpass("R2 Access Key ID (hidden): ").strip()
    secret = getpass.getpass("R2 Secret Access Key (hidden): ").strip()
    if not (account_id and bucket and access and secret):
        sys.exit("error: account ID, bucket and both keys are required")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    kf = key_file(name)
    kf.write_text(json.dumps({"access_key_id": access, "secret_access_key": secret}))
    os.chmod(kf, 0o600)
    cfg.setdefault("locations", {})[name] = loc
    if not cfg.get("default") or len(cfg["locations"]) == 1:
        cfg["default"] = name
    elif ask(f"Make {name!r} the default location? (y/n)", "n").lower().startswith("y"):
        cfg["default"] = name
    save_config(cfg)
    print(f"\nSaved location {name!r} (default: {cfg['default']!r}). Running a quick test...")
    try:
        self_test(name, loc)
        print("\nAll good. The TikTok skill can now upload clips here.")
    except StorageError as e:
        print(f"\nTest failed: {e}\nFix the setting in Cloudflare and run `storage.py test --location {name}`.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup").set_defaults(fn=cmd_setup)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    d = sub.add_parser("default"); d.add_argument("name"); d.set_defaults(fn=cmd_default)
    t = sub.add_parser("test"); t.add_argument("--location"); t.set_defaults(fn=cmd_test)
    a = p.parse_args()
    try:
        a.fn(a)
    except StorageError as e:
        sys.exit(f"error [{e.code}]: {e}")


if __name__ == "__main__":
    main()
