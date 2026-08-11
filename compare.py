#!/usr/bin/env python3
"""
compare.py — CLI entry point for the LeetCode Profile Comparator.

Compare two LeetCode profiles and list problems in any of four categories,
filterable by difficulty / topic tag / title search.

Typical use (the catch-up list):

    python compare.py --me alice --friend bob --diff friend-only --difficulty Hard

Getting the FULL, accurate solved set for a user requires that user's own
LEETCODE_SESSION cookie (LeetCode does not expose full solved lists publicly).
Supply cookies via environment variables (recommended, keeps them off the
command line and shell history):

    setx LEETCODE_COOKIE_ME    "LEETCODE_SESSION=eyJ...."      (your cookie)
    setx LEETCODE_COOKIE_FRIEND "LEETCODE_SESSION=eyJ...."     (friend's)

or a local config.json (see config.example.json). Without a cookie a user is
fetched in "public" mode: exact difficulty counts, but only their last 20
solved problems are known, so the per-problem diff is approximate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import cache
import fetch
from diff import CATEGORIES, categorize, partial_warning
from filters import apply_filters, sort_problems

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "config.json")

CATEGORY_LABEL = {
    "friend-only": "Friend solved, you haven't  (catch-up list)",
    "me-only": "You solved, friend hasn't",
    "both": "Both solved",
    "neither": "Neither solved",
}


# --------------------------------------------------------------------------- #
# Config / cookie resolution
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"warning: could not read config.json ({e})", file=sys.stderr)
    return {}


def resolve(value: str | None, env_key: str, cfg_val: str | None) -> str | None:
    """Precedence: explicit CLI value > environment variable > config.json."""
    return value or os.environ.get(env_key) or cfg_val


# --------------------------------------------------------------------------- #
# Data loading (with cache)
# --------------------------------------------------------------------------- #
def get_ratings(refresh: bool) -> dict[str, int]:
    """Load zerotrac's {slug: rating} map, from cache or a fresh download.

    Ratings are an enhancement, not core data, so a failed download degrades
    gracefully: we fall back to any cached copy (however old) and finally to an
    empty map (everything shows as unrated) rather than failing the whole run.
    """
    if not refresh:
        cached = cache.load_ratings()
        if cached is not None:
            return cached
    print("Fetching problem ratings (zerotrac) ...", file=sys.stderr)
    try:
        ratings = fetch.fetch_ratings()
    except fetch.LeetCodeError as e:
        stale = cache.load_ratings(ttl=10**12)  # any cached copy, ignore age
        note = " — using cached copy" if stale else " — continuing unrated"
        print(f"  ! {e}{note}", file=sys.stderr)
        return stale or {}
    cache.save_ratings(ratings)
    return ratings


def get_catalog(refresh: bool) -> list[fetch.Problem]:
    catalog = None if refresh else cache.load_catalog()
    if catalog is None:
        print("Fetching problem catalog from LeetCode ...", file=sys.stderr)

        def prog(done, total):
            print(f"\r  {done}/{total} problems", end="", file=sys.stderr)

        catalog = fetch.fetch_catalog(progress=prog)
        print("", file=sys.stderr)
        cache.save_catalog(catalog)

    # Join zerotrac ratings by slug (idempotent — safe on cached catalogs too).
    ratings = get_ratings(refresh)
    for p in catalog:
        p.rating = ratings.get(p.slug)
    return catalog


def load_solved_file(path: str) -> tuple[str | None, list[str]]:
    """Read a share-export file; return (username_or_None, slugs).

    Accepts {username?, solved_slugs:[...]} or a bare list of slugs.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        slugs, username = data, None
    else:
        slugs, username = data.get("solved_slugs"), data.get("username")
    if not isinstance(slugs, list) or not slugs:
        raise fetch.LeetCodeError(f"{path}: no 'solved_slugs' list found.")
    return (username or None), slugs


def get_user(username: str, cookie: str | None,
             refresh: bool) -> fetch.UserSolved:
    # A cookie means we want the authoritative full set — bypass any public
    # cache entry for that user unless it is already a "full" entry.
    cached = cache.load_user(username)
    if not refresh and cached and (cached.mode == "full" or not cookie):
        return cached
    label = "full (cookie)" if cookie else "public"
    print(f"Fetching '{username}' [{label}] ...", file=sys.stderr)
    try:
        user = fetch.fetch_user(username, cookie=cookie)
    except fetch.LeetCodeError:
        # A dead/expired cookie makes fetch_user raise rather than degrade.
        # If we still have a full snapshot from before it expired, prefer
        # serving that (stale but complete) over failing the whole run.
        if cached and cached.mode == "full":
            print(f"  ! cookie for '{username}' no longer works — using last "
                  f"full snapshot from cache (may be out of date).",
                  file=sys.stderr)
            return cached
        raise
    if cached and cached.mode == "full" and user.mode != "full":
        # The re-fetch fell back to public mode. Keep serving the last known
        # full snapshot instead of downgrading silently.
        print(f"  ! cookie for '{username}' no longer works — using last full "
              f"snapshot from cache (may be out of date) instead of public mode.",
              file=sys.stderr)
        return cached
    cache.save_user(user)
    return user


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def render_table(problems: list[fetch.Problem], limit: int | None) -> str:
    if not problems:
        return "  (no problems match)\n"
    shown = problems[:limit] if limit else problems
    rows = []
    for i, p in enumerate(shown, 1):
        rows.append((
            str(i),
            _truncate(p.title, 42),
            p.difficulty,
            str(p.rating) if p.rating is not None else "—",
            f"{p.ac_rate:.1f}%",
            _truncate(", ".join(p.tags), 40),
        ))
    headers = ("#", "Title", "Diff", "Rating", "AC%", "Tags")
    widths = [len(h) for h in headers]
    for r in rows:
        for j, cell in enumerate(r):
            widths[j] = max(widths[j], len(cell))

    def fmt(cells):
        return "  ".join(c.ljust(widths[j]) for j, c in enumerate(cells))

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines += [fmt(r) for r in rows]
    if limit and len(problems) > limit:
        lines.append(f"... {len(problems) - limit} more (raise --limit to see)")
    return "\n".join(lines) + "\n"


def counts_line(user: fetch.UserSolved) -> str:
    c = user.counts
    tag = "" if user.mode == "full" else "  [public: counts exact, list≈last20]"
    return (f"  {user.username:<20} "
            f"All {c.get('All', 0):>4}  "
            f"Easy {c.get('Easy', 0):>4}  "
            f"Med {c.get('Medium', 0):>4}  "
            f"Hard {c.get('Hard', 0):>4}{tag}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Compare two LeetCode profiles and list the gap.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--me", help="your LeetCode username")
    p.add_argument("--friend", help="friend's LeetCode username")
    p.add_argument("--me-cookie", help="your LEETCODE_SESSION cookie value")
    p.add_argument("--friend-cookie", help="friend's LEETCODE_SESSION cookie")
    p.add_argument("--me-solved", metavar="FILE",
                   help="import your solved list from a share-export .json")
    p.add_argument("--friend-solved", metavar="FILE",
                   help="import friend's solved list from a share-export .json")
    p.add_argument("--diff", choices=CATEGORIES, default="friend-only",
                   help="which category to list (default: friend-only)")
    p.add_argument("--difficulty", choices=["Easy", "Medium", "Hard"],
                   help="filter by difficulty")
    p.add_argument("--tag", action="append", default=[],
                   help="filter by topic tag slug (repeatable), "
                        "e.g. --tag dynamic-programming --tag graph")
    p.add_argument("--all-tags", action="store_true",
                   help="require ALL given tags (default: match any)")
    p.add_argument("--search", help="case-insensitive title substring filter")
    p.add_argument("--min-rating", type=int, metavar="N",
                   help="only problems with zerotrac rating >= N (e.g. 1759)")
    p.add_argument("--max-rating", type=int, metavar="N",
                   help="only problems with zerotrac rating <= N (e.g. 2434)")
    p.add_argument("--rating", choices=["rated", "unrated"],
                   help="only rated (contest) problems, or only unrated ones")
    p.add_argument("--sort", choices=["difficulty", "title", "acrate", "rating"],
                   default="difficulty", help="sort key (default: difficulty)")
    p.add_argument("--desc", action="store_true", help="sort descending")
    p.add_argument("--limit", type=int, default=50,
                   help="max rows to print (0 = all; default 50)")
    p.add_argument("--refresh", action="store_true",
                   help="ignore cache and re-fetch from LeetCode")
    p.add_argument("--list-tags", action="store_true",
                   help="print all valid tag slugs (with problem counts) & exit")
    return p


def print_tags(catalog: list[fetch.Problem]) -> None:
    counts: dict[str, int] = {}
    for p in catalog:
        for t in p.tags:
            counts[t] = counts.get(t, 0) + 1
    print("Valid --tag slugs (problem count):\n")
    for tag, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {tag:<28} {n}")


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252; make Unicode (≈, …) safe to print.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    cfg = load_config()

    if args.list_tags:
        print_tags(get_catalog(args.refresh))
        return 0

    # Pre-load any import files so their embedded username can fill in --me/--friend.
    try:
        me_import = load_solved_file(args.me_solved) if args.me_solved else None
        friend_import = (load_solved_file(args.friend_solved)
                         if args.friend_solved else None)
    except (fetch.LeetCodeError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    me_name = (resolve(args.me, "LEETCODE_USER_ME", cfg.get("me"))
               or (me_import[0] if me_import else None))
    friend_name = (resolve(args.friend, "LEETCODE_USER_FRIEND", cfg.get("friend"))
                   or (friend_import[0] if friend_import else None))
    if not me_name or not friend_name:
        print("error: need a username for each side — pass --me/--friend, set them "
              "in config.json, or import a file that includes the username.",
              file=sys.stderr)
        return 2

    me_cookie = resolve(args.me_cookie, "LEETCODE_COOKIE_ME",
                        cfg.get("me_cookie"))
    friend_cookie = resolve(args.friend_cookie, "LEETCODE_COOKIE_FRIEND",
                            cfg.get("friend_cookie"))

    try:
        catalog = get_catalog(args.refresh)
        if me_import:
            me = fetch.user_from_slugs(me_name, me_import[1])
            cache.save_user(me)
        else:
            me = get_user(me_name, me_cookie, args.refresh)
        if friend_import:
            friend = fetch.user_from_slugs(friend_name, friend_import[1])
            cache.save_user(friend)
        else:
            friend = get_user(friend_name, friend_cookie, args.refresh)
    except (fetch.LeetCodeError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    buckets = categorize(catalog, me, friend)
    problems = buckets[args.diff]
    problems = apply_filters(problems,
                             difficulty=args.difficulty,
                             tags=args.tag,
                             search=args.search,
                             match_all_tags=args.all_tags,
                             rating_min=args.min_rating,
                             rating_max=args.max_rating,
                             rating_mode=args.rating or "all")
    problems = sort_problems(problems, key=args.sort, reverse=args.desc)

    # ---- output -------------------------------------------------------- #
    print()
    print("Solved totals (exact, from LeetCode):")
    print(counts_line(me))
    print(counts_line(friend))
    warn = partial_warning(me, friend)
    if warn:
        print()
        print("  ! " + warn)
    print()

    active = []
    if args.difficulty:
        active.append(args.difficulty)
    if args.tag:
        active.append(("all of " if args.all_tags else "any of ")
                      + "/".join(args.tag))
    if args.search:
        active.append(f'title~"{args.search}"')
    if args.min_rating is not None or args.max_rating is not None:
        lo = args.min_rating if args.min_rating is not None else "…"
        hi = args.max_rating if args.max_rating is not None else "…"
        active.append(f"rating {lo}–{hi}")
    if args.rating:
        active.append(args.rating)
    flt = f"   [filters: {', '.join(active)}]" if active else ""

    print(f"== {CATEGORY_LABEL[args.diff]} ==   {len(problems)} problems{flt}")
    print()
    limit = None if args.limit == 0 else args.limit
    print(render_table(problems, limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
