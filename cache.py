"""
cache.py — Tiny JSON file cache so repeat runs don't re-hit LeetCode.

Three things are cached, all under ./cache/ :
  * catalog.json      — the full problem list (rarely changes). Refreshed on
                        demand or when older than CATALOG_TTL.
  * ratings.json      — zerotrac's {slug: rating} map (changes after each
                        contest). Refreshed on demand or when past RATINGS_TTL.
  * users/<name>.json — a user's solved set (changes as they practice).

Nothing here is secret: cookies are never written to disk.
"""

from __future__ import annotations

import json
import os
import time

from fetch import Problem, UserSolved

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
CATALOG_PATH = os.path.join(CACHE_DIR, "catalog.json")
RATINGS_PATH = os.path.join(CACHE_DIR, "ratings.json")
USERS_DIR = os.path.join(CACHE_DIR, "users")

CATALOG_TTL = 7 * 24 * 3600    # a week
RATINGS_TTL = 24 * 3600        # a day (updates after each weekly/biweekly contest)
USER_TTL = 6 * 3600            # six hours


def _ensure_dirs() -> None:
    os.makedirs(USERS_DIR, exist_ok=True)


def _fresh(path: str, ttl: int) -> bool:
    return os.path.exists(path) and (time.time() - os.path.getmtime(path)) < ttl


# ---- catalog ------------------------------------------------------------- #
def load_catalog(ttl: int = CATALOG_TTL) -> list[Problem] | None:
    if not _fresh(CATALOG_PATH, ttl):
        return None
    with open(CATALOG_PATH, encoding="utf-8") as f:
        return [Problem.from_dict(d) for d in json.load(f)]


def save_catalog(problems: list[Problem]) -> None:
    _ensure_dirs()
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump([p.to_dict() for p in problems], f)


# ---- ratings (zerotrac) -------------------------------------------------- #
def load_ratings(ttl: int = RATINGS_TTL) -> dict[str, int] | None:
    """Return the cached {slug: rating} map, or None if missing/stale.

    Pass a very large ttl to force-read a stale copy (used as a fallback when a
    fresh download fails — a slightly old rating map beats none at all).
    """
    if not _fresh(RATINGS_PATH, ttl):
        return None
    with open(RATINGS_PATH, encoding="utf-8") as f:
        return {k: int(v) for k, v in json.load(f).items()}


def save_ratings(ratings: dict[str, int]) -> None:
    _ensure_dirs()
    with open(RATINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(ratings, f)


# ---- users --------------------------------------------------------------- #
def _user_path(username: str) -> str:
    safe = "".join(c for c in username.lower() if c.isalnum() or c in "-_.")
    return os.path.join(USERS_DIR, f"{safe}.json")


def load_user(username: str, ttl: int = USER_TTL) -> UserSolved | None:
    path = _user_path(username)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    # A "full" snapshot (cookie-derived) never goes stale on its own — it's
    # strictly better than any public-mode re-fetch, so we keep it around
    # indefinitely rather than letting the TTL discard it once the cookie
    # that produced it expires. Only "public" entries age out.
    if d["mode"] != "full" and not _fresh(path, ttl):
        return None
    return UserSolved(
        username=d["username"],
        mode=d["mode"],
        solved_slugs=set(d["solved_slugs"]),
        counts=d["counts"],
    )


def save_user(user: UserSolved) -> None:
    _ensure_dirs()
    with open(_user_path(user.username), "w", encoding="utf-8") as f:
        json.dump({
            "username": user.username,
            "mode": user.mode,
            "solved_slugs": sorted(user.solved_slugs),
            "counts": user.counts,
        }, f)
