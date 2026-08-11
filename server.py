#!/usr/bin/env python3
"""
server.py — Local web UI for the LeetCode Profile Comparator.

Runs a small stdlib HTTP server (no third-party packages) that serves a single
page and a JSON API backed by the same fetch/diff/filters modules as the CLI.

    python server.py                # then open http://localhost:8010
    python server.py --port 9000

Cookies (for full/accurate solved lists) can be entered in the page and are sent
to THIS local server only, via POST body — never placed in a URL or logged. If
left blank, the server falls back to LEETCODE_COOKIE_ME / LEETCODE_COOKIE_FRIEND
env vars or config.json, exactly like the CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cache
import fetch
from compare import get_catalog, load_config, resolve
from diff import CATEGORIES, categorize, partial_warning
from filters import apply_filters, sort_problems

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(HERE, "web", "index.html")
SHARE_PATH = os.path.join(HERE, "web", "share.html")

# Fetching a user with a cookie hits LeetCode (paginated). Guard shared cache /
# network work so concurrent browser requests don't stampede.
_LOCK = threading.Lock()


def _fetch_user_cached(username: str, cookie: str | None,
                       refresh: bool) -> fetch.UserSolved:
    """Like compare.get_user but cookie-aware and lock-protected."""
    with _LOCK:
        if not refresh:
            cached = cache.load_user(username)
            if cached and (cached.mode == "full" or not cookie):
                return cached
        user = fetch.fetch_user(username, cookie=cookie)
        cache.save_user(user)
        return user


def _int_or_none(v) -> int | None:
    """Coerce a rating-bound field (may be "", None, or a number) to int|None."""
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def run_compare(payload: dict) -> dict:
    """Core API handler shared by the web UI. Returns a JSON-able dict."""
    cfg = load_config()
    me_name = (payload.get("me") or "").strip() or cfg.get("me")
    friend_name = (payload.get("friend") or "").strip() or cfg.get("friend")
    if not me_name or not friend_name:
        raise ValueError("Both your username and your friend's are required.")

    me_cookie = resolve(payload.get("me_cookie"), "LEETCODE_COOKIE_ME",
                        cfg.get("me_cookie"))
    friend_cookie = resolve(payload.get("friend_cookie"),
                            "LEETCODE_COOKIE_FRIEND", cfg.get("friend_cookie"))
    refresh = bool(payload.get("refresh"))

    # Imported solved lists (from the browser snippet) take priority over
    # cookies/public fetching and yield an exact "full" set with no credentials.
    me_solved = payload.get("me_solved")
    friend_solved = payload.get("friend_solved")

    catalog = get_catalog(refresh)

    def load_side(name, imported, cookie):
        if imported:
            user = fetch.user_from_slugs(name, imported)
            cache.save_user(user)
            return user
        return _fetch_user_cached(name, cookie, refresh)

    me = load_side(me_name, me_solved, me_cookie)
    friend = load_side(friend_name, friend_solved, friend_cookie)

    diff_key = payload.get("diff", "friend-only")
    if diff_key not in CATEGORIES:
        diff_key = "friend-only"

    buckets = categorize(catalog, me, friend)
    rating_mode = payload.get("rating_mode") or "all"
    if rating_mode not in ("all", "rated", "unrated"):
        rating_mode = "all"
    problems = apply_filters(
        buckets[diff_key],
        difficulty=payload.get("difficulty") or None,
        tags=payload.get("tags") or None,
        search=payload.get("search") or None,
        match_all_tags=bool(payload.get("all_tags")),
        rating_min=_int_or_none(payload.get("rating_min")),
        rating_max=_int_or_none(payload.get("rating_max")),
        rating_mode=rating_mode,
    )
    problems = sort_problems(problems, key=payload.get("sort", "difficulty"),
                             reverse=bool(payload.get("desc")))

    # Category sizes for the summary chips (unfiltered).
    sizes = {k: len(v) for k, v in buckets.items()}

    # Rating range available in the current (pre-filter) bucket — lets the UI
    # show sensible min/max hints for the numeric range inputs.
    rated_here = [p.rating for p in buckets[diff_key] if p.rating is not None]
    rating_span = ([min(rated_here), max(rated_here)] if rated_here else None)

    return {
        "me": {"username": me.username, "mode": me.mode, "counts": me.counts},
        "friend": {"username": friend.username, "mode": friend.mode,
                   "counts": friend.counts},
        "warning": partial_warning(me, friend),
        "diff": diff_key,
        "sizes": sizes,
        "rating_span": rating_span,
        "count": len(problems),
        "problems": [
            {"slug": p.slug, "title": p.title, "difficulty": p.difficulty,
             "tags": p.tags, "ac_rate": p.ac_rate, "rating": p.rating,
             "url": f"https://leetcode.com/problems/{p.slug}/"}
            for p in problems
        ],
    }


def get_tags() -> list[dict]:
    counts: dict[str, int] = {}
    for p in get_catalog(False):
        for t in p.tags:
            counts[t] = counts.get(t, 0) + 1
    return [{"slug": t, "count": n}
            for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


class Handler(BaseHTTPRequestHandler):
    server_version = "leetcode-comparator/1.0"

    # ---- helpers ---- #
    def _send_json(self, obj: dict | list, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, path: str = INDEX_PATH) -> None:
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(500, os.path.basename(path) + " missing")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- routes ---- #
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_html()
        elif self.path in ("/share", "/share.html"):
            self._send_html(SHARE_PATH)
        elif self.path.startswith("/api/tags"):
            try:
                self._send_json(get_tags())
            except fetch.LeetCodeError as e:
                self._send_json({"error": str(e)}, 502)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api/compare":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json({"error": "Invalid request body."}, 400)
            return
        try:
            self._send_json(run_compare(payload))
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except fetch.LeetCodeError as e:
            self._send_json({"error": str(e)}, 502)
        except Exception as e:  # last-resort: don't leak a stack trace to UI
            self._send_json({"error": f"Unexpected error: {e}"}, 500)

    # Keep cookies/usernames out of the terminal log noise.
    def log_message(self, fmt, *args):
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Web UI for LeetCode comparator.")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", action="store_true",
                    help="don't auto-open the browser")
    args = ap.parse_args(argv)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"LeetCode Comparator UI running at {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
