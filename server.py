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
import credstore
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


def _stale_note(err: Exception, cookie: str | None) -> str:
    """Explain, in one line, why the UI is looking at saved data.

    Deliberately reassuring: nothing is broken, the comparison still ran, and
    the only thing lost is freshness.
    """
    if cookie:
        return ("Cookie rejected by LeetCode (it has probably expired) — "
                "showing the last list saved for this user. Paste a fresh "
                "cookie above whenever you want to update it.")
    detail = str(err).strip()
    if len(detail) > 140:
        detail = detail[:137] + "..."
    return (f"Couldn't reach LeetCode — showing the last list saved for this "
            f"user. ({detail})")


def _fetch_user_cached(username: str, cookie: str | None,
                       refresh: bool) -> tuple[fetch.UserSolved, str | None]:
    """Like compare.get_user but cookie-aware, lock-protected and fail-soft.

    Returns (user, stale_note). Once a user has been fetched, the snapshot on
    disk is the source of truth: an expired cookie or a dead connection
    downgrades the run to "here's what we saved last time" with a note, rather
    than failing the whole comparison. Only a user we've never seen can error.
    """
    with _LOCK:
        if not refresh:
            cached = cache.load_user(username)
            if cached and (cached.mode == "full" or not cookie):
                return cached, None
        try:
            user = fetch.fetch_user(username, cookie=cookie)
        except (fetch.LeetCodeError, OSError) as e:
            saved = cache.load_user(username, ttl=float("inf"))
            if saved is None:
                raise
            return saved, _stale_note(e, cookie)
        cache.save_user(user)
        return user, None


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
    # Per-side "Update" buttons refresh one profile without re-fetching both.
    refresh_me = refresh or bool(payload.get("refresh_me"))
    refresh_friend = refresh or bool(payload.get("refresh_friend"))

    # Imported solved lists (from the browser snippet) take priority over
    # cookies/public fetching and yield an exact "full" set with no credentials.
    me_solved = payload.get("me_solved")
    friend_solved = payload.get("friend_solved")

    catalog = get_catalog(refresh)

    def load_side(name, imported, cookie, want_refresh):
        if imported:
            user = fetch.user_from_slugs(name, imported)
            cache.save_user(user)
            return user, None
        return _fetch_user_cached(name, cookie, want_refresh)

    me, me_stale = load_side(me_name, me_solved, me_cookie, refresh_me)
    friend, friend_stale = load_side(friend_name, friend_solved, friend_cookie,
                                     refresh_friend)

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
        "me": {"username": me.username, "mode": me.mode, "counts": me.counts,
               "saved_at": cache.saved_at(me.username), "stale": me_stale},
        "friend": {"username": friend.username, "mode": friend.mode,
                   "counts": friend.counts,
                   "saved_at": cache.saved_at(friend.username),
                   "stale": friend_stale},
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


# --------------------------------------------------------------------------- #
# Saved settings (usernames + cookies) — so the UI asks only once
# --------------------------------------------------------------------------- #
def _cookie_status(raw_cfg: dict, key: str) -> dict:
    """Describe a saved cookie without ever revealing it.

    `usable: False` means something is stored but this machine/account can't
    decrypt it (a config.json copied from elsewhere), which the UI reports as
    "paste a fresh one" rather than a hard error.
    """
    stored = raw_cfg.get(key + "_enc") or raw_cfg.get(key)
    if not stored:
        return {"saved": False, "usable": False, "hint": ""}
    plain = credstore.unprotect(stored)
    return {"saved": True, "usable": bool(plain),
            "hint": credstore.hint(plain)}


def get_settings() -> dict:
    raw = credstore.read_config_raw()
    return {
        "me": raw.get("me", ""),
        "friend": raw.get("friend", ""),
        "me_cookie": _cookie_status(raw, "me_cookie"),
        "friend_cookie": _cookie_status(raw, "friend_cookie"),
        "saved_users": cache.list_users(),
        "storage": credstore.protected_kind(),
    }


def save_settings(payload: dict) -> dict:
    """Persist whatever the page sent. Blank fields leave saved values alone."""
    credstore.update_config(
        me=(payload.get("me") or "").strip(),
        friend=(payload.get("friend") or "").strip(),
        me_cookie=(payload.get("me_cookie") or "").strip(),
        friend_cookie=(payload.get("friend_cookie") or "").strip(),
    )
    return get_settings()


def forget_settings() -> dict:
    """Drop saved usernames and cookies. Saved solved-lists are kept."""
    credstore.update_config(clear=True)
    return get_settings()


def import_solved(payload: dict) -> dict:
    """Accept a solved list pushed by the /share bookmarklet.

    Same result as importing the downloaded .json by hand, minus the download
    and the file picker — and it costs no cookie at all.
    """
    username = (payload.get("username") or "").strip()
    slugs = payload.get("solved_slugs")
    if not username or not isinstance(slugs, list) or not slugs:
        raise ValueError("Expected {username, solved_slugs: [...]}.")
    slugs = [s for s in slugs if isinstance(s, str) and s]
    if not slugs:
        raise ValueError("solved_slugs contained no usable problem slugs.")
    try:
        user = fetch.user_from_slugs(username, slugs)
    except (fetch.LeetCodeError, OSError):
        # user_from_slugs only goes to the network for the public counts that
        # decorate the summary bars. The solved list itself is already in hand,
        # so keep it rather than throwing away a good export over a blip.
        user = fetch.UserSolved(username, "full", set(slugs), {})
    with _LOCK:
        cache.save_user(user)
    return {"ok": True, "username": user.username, "solved": len(slugs)}


def get_tags() -> list[dict]:
    counts: dict[str, int] = {}
    for p in get_catalog(False):
        for t in p.tags:
            counts[t] = counts.get(t, 0) + 1
    return [{"slug": t, "count": n}
            for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


# The bookmarklet runs on leetcode.com and posts here, so /api/import — and
# only /api/import — answers cross-origin. Everything else stays same-origin so
# no web page can read the saved cookie or trigger a compare.
IMPORT_ORIGINS = ("https://leetcode.com", "https://www.leetcode.com")


class Handler(BaseHTTPRequestHandler):
    server_version = "leetcode-comparator/1.0"

    # ---- helpers ---- #
    def _cors(self) -> None:
        """Allow the /share bookmarklet's origin on /api/import, nothing else.

        Scoped by path on purpose: without that, a leetcode.com tab could read
        GET /api/settings (a simple request, no preflight to stop it) and learn
        the saved usernames.
        """
        if self.path.rstrip("/") != "/api/import":
            return
        origin = self.headers.get("Origin", "")
        if origin in IMPORT_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "content-type")
            # Chrome's Private Network Access check: a public page reaching
            # 127.0.0.1 is preflighted and needs this to go through.
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "86400")

    def _send_json(self, obj: dict | list, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self._cors()
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

    def _read_json(self) -> dict | None:
        """Parse a JSON body, or answer 400 and return None."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json({"error": "Invalid request body."}, 400)
            return None
        if not isinstance(payload, dict):
            self._send_json({"error": "Invalid request body."}, 400)
            return None
        return payload

    def _run(self, fn, *args) -> None:
        """Call an API function and map its failures onto status codes."""
        try:
            self._send_json(fn(*args))
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except fetch.LeetCodeError as e:
            self._send_json({"error": str(e)}, 502)
        except OSError as e:
            self._send_json({"error": f"Could not write config.json: {e}"}, 500)
        except Exception as e:  # last-resort: don't leak a stack trace to UI
            self._send_json({"error": f"Unexpected error: {e}"}, 500)

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
        elif self.path.startswith("/api/settings"):
            self._run(get_settings)
        else:
            self.send_error(404)

    def do_POST(self):
        route = {"/api/compare": run_compare,
                 "/api/settings": save_settings,
                 "/api/import": import_solved}.get(self.path.rstrip("/"))
        if route is None:
            self.send_error(404)
            return
        payload = self._read_json()
        if payload is None:
            return
        self._run(route, payload)

    def do_DELETE(self):
        """Forget saved usernames and cookies."""
        if self.path.rstrip("/") != "/api/settings":
            self.send_error(404)
            return
        self._run(forget_settings)

    def do_OPTIONS(self):
        """Preflight for the bookmarklet's cross-origin POST to /api/import."""
        if self.path.rstrip("/") != "/api/import":
            self.send_error(404)
            return
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

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
