"""
fetch.py — All LeetCode GraphQL access lives here.

LeetCode has no official public API. This module talks to the same unofficial
GraphQL endpoint (https://leetcode.com/graphql) that leetcode.com itself uses.
It can change/break without notice, so everything network-related is isolated
in this one file.

Key reality this module works around:
  * The FULL list of problems a user solved is NOT public. It is only returned
    when the request carries THAT user's own `LEETCODE_SESSION` cookie (each
    problem then comes back with status == "ac").
  * Without a cookie we can only get: total solved counts per difficulty, and
    the user's 20 most-recent accepted problems (hard cap of 20).

So each user is fetched in one of two modes:
  * "full"   -> a session cookie was supplied; we get the complete solved set.
  * "public" -> no cookie; we get counts + last-20 only (an approximation).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

GRAPHQL_URL = "https://leetcode.com/graphql"
# categorySlug that covers the standard algorithm/database/etc. problem set.
CATEGORY = "all-code-essentials"

# zerotrac's community-maintained per-problem contest ratings (Elo-like difficulty
# scores, ~1000–3500). One tab-separated file, refreshed after each contest.
# Columns: Rating \t ID \t Title \t Title ZH \t Title Slug \t Contest Slug \t Index
# Only CONTEST problems are rated; classic problems (Two Sum, LIS, …) are absent.
RATINGS_URL = ("https://raw.githubusercontent.com/zerotrac/"
               "leetcode_problem_rating/main/ratings.txt")


class LeetCodeError(RuntimeError):
    """Raised when the endpoint returns an error or unexpected shape."""


def normalize_cookie(raw: str | None) -> str | None:
    """Accept a pasted cookie in any of the common shapes and normalize it.

    Users may paste the whole cookie header ("LEETCODE_SESSION=..; csrftoken=..")
    or just the bare LEETCODE_SESSION token value. Return a usable Cookie header,
    or None if empty.
    """
    if not raw:
        return None
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        return None
    # Already a name=value cookie string -> use as-is.
    if "=" in raw:
        return raw
    # Bare token value -> wrap it.
    return f"LEETCODE_SESSION={raw}"


@dataclass
class Problem:
    slug: str
    title: str
    difficulty: str  # "Easy" | "Medium" | "Hard"
    tags: list[str] = field(default_factory=list)
    ac_rate: float = 0.0
    # zerotrac contest rating (rounded int) — None when the problem is unrated
    # (never appeared in a contest). Joined in from the ratings file by slug.
    rating: int | None = None

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "difficulty": self.difficulty,
            "tags": self.tags,
            "ac_rate": self.ac_rate,
            "rating": self.rating,
        }

    @staticmethod
    def from_dict(d: dict) -> "Problem":
        r = d.get("rating")
        return Problem(
            slug=d["slug"],
            title=d["title"],
            difficulty=d["difficulty"],
            tags=list(d.get("tags", [])),
            ac_rate=float(d.get("ac_rate", 0.0)),
            rating=int(r) if r is not None else None,
        )


@dataclass
class UserSolved:
    username: str
    mode: str                       # "full" | "public"
    solved_slugs: set[str]          # slugs we know the user solved
    counts: dict[str, int]          # {"All": n, "Easy": n, "Medium": n, "Hard": n}

    @property
    def is_partial(self) -> bool:
        """True when solved_slugs is only an approximation (public mode)."""
        return self.mode != "full"


# --------------------------------------------------------------------------- #
# Low-level request helper
# --------------------------------------------------------------------------- #
def graphql(query: str, variables: dict, cookie: str | None = None,
            *, retries: int = 3) -> dict:
    """POST a GraphQL query and return the `data` object.

    `cookie` is the raw value of the LEETCODE_SESSION cookie (optionally with a
    csrftoken appended as "LEETCODE_SESSION=..; csrftoken=..").
    """
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Referer": "https://leetcode.com",
        "User-Agent": "leetcode-comparator/1.0",
    }
    if cookie:
        headers["Cookie"] = cookie
        # If a csrftoken is present in the cookie, mirror it as a header.
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("csrftoken="):
                headers["x-csrftoken"] = part[len("csrftoken="):]

    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(GRAPHQL_URL, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = LeetCodeError(f"HTTP {e.code} from LeetCode: {e.reason}")
            # 429 / 5xx -> back off and retry
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last_err
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = LeetCodeError(f"Network error talking to LeetCode: {e}")
            time.sleep(1.5 * (attempt + 1))
            continue

        if payload.get("errors"):
            # Surface the human-readable message, not the raw JSON envelope.
            msgs = [e.get("message", "") for e in payload["errors"]]
            raise LeetCodeError("; ".join(m for m in msgs if m)[:500]
                                or json.dumps(payload["errors"])[:500])
        return payload.get("data", {})

    raise last_err or LeetCodeError("Request failed")


# --------------------------------------------------------------------------- #
# Catalog: every problem with tags + difficulty (no auth needed)
# --------------------------------------------------------------------------- #
_CATALOG_QUERY = """
query catalog($c: String!, $limit: Int!, $skip: Int!) {
  questionList(categorySlug: $c, limit: $limit, skip: $skip, filters: {}) {
    total: totalNum
    questions: data {
      title
      titleSlug
      difficulty
      acRate
      topicTags { slug }
    }
  }
}
"""


def fetch_catalog(page_size: int = 500,
                  progress=lambda done, total: None) -> list[Problem]:
    """Fetch the complete problem catalog (title, slug, difficulty, tags, acRate).

    One paginated query handles all tags in bulk — no N+1 per-problem calls.
    """
    problems: list[Problem] = []
    skip = 0
    total = None
    while True:
        data = graphql(_CATALOG_QUERY,
                       {"c": CATEGORY, "limit": page_size, "skip": skip})
        block = data["questionList"]
        total = block["total"] if total is None else total
        rows = block["questions"]
        for q in rows:
            problems.append(Problem(
                slug=q["titleSlug"],
                title=q["title"],
                difficulty=q["difficulty"],
                tags=[t["slug"] for t in q.get("topicTags", [])],
                ac_rate=round(float(q.get("acRate") or 0.0), 2),
            ))
        skip += len(rows)
        progress(min(skip, total), total)
        if not rows or skip >= total:
            break
    return problems


# --------------------------------------------------------------------------- #
# Problem ratings (zerotrac) — no auth, one static tab-separated file
# --------------------------------------------------------------------------- #
def fetch_ratings() -> dict[str, int]:
    """Download zerotrac's per-problem contest ratings, keyed by title slug.

    Returns {titleSlug: rounded_rating}. The slug is the stable join key that
    matches leetcode.com/problems/<slug>/, so joining needs no fuzzy matching.
    Only contest problems are present; unrated problems are simply absent (the
    caller treats a missing slug as rating == None).
    """
    req = urllib.request.Request(
        RATINGS_URL, headers={"User-Agent": "leetcode-comparator/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise LeetCodeError(f"HTTP {e.code} fetching ratings: {e.reason}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise LeetCodeError(f"Network error fetching problem ratings: {e}")

    ratings: dict[str, int] = {}
    for line in text.splitlines()[1:]:          # skip the header row
        parts = line.split("\t")
        # Columns: Rating, ID, Title, Title ZH, Title Slug, Contest Slug, Index
        if len(parts) >= 5 and parts[0] and parts[4]:
            try:
                ratings[parts[4]] = round(float(parts[0]))
            except ValueError:
                continue                         # skip a malformed row, not all
    if not ratings:
        raise LeetCodeError("Ratings file downloaded but no rows parsed.")
    return ratings


# --------------------------------------------------------------------------- #
# Per-user solved data
# --------------------------------------------------------------------------- #
_COUNTS_QUERY = """
query counts($u: String!) {
  matchedUser(username: $u) {
    username
    submitStatsGlobal { acSubmissionNum { difficulty count } }
  }
}
"""

_RECENT_QUERY = """
query recent($u: String!, $l: Int!) {
  recentAcSubmissionList(username: $u, limit: $l) { titleSlug }
}
"""

_SOLVED_QUERY = """
query solved($c: String!, $limit: Int!, $skip: Int!) {
  questionList(categorySlug: $c, limit: $limit, skip: $skip,
               filters: {status: AC}) {
    total: totalNum
    questions: data { titleSlug }
  }
}
"""


def fetch_counts(username: str) -> dict[str, int]:
    """Public per-difficulty solved counts for a username."""
    data = graphql(_COUNTS_QUERY, {"u": username})
    user = data.get("matchedUser")
    if not user:
        raise LeetCodeError(f"User '{username}' not found or profile private.")
    counts = {}
    for row in user["submitStatsGlobal"]["acSubmissionNum"]:
        counts[row["difficulty"]] = row["count"]
    return counts


def _fetch_solved_full(username: str, cookie: str,
                       page_size: int = 500) -> set[str]:
    """Complete solved set for the cookie's OWNER (status: AC filter)."""
    slugs: set[str] = set()
    skip = 0
    total = None
    while True:
        data = graphql(_SOLVED_QUERY,
                       {"c": CATEGORY, "limit": page_size, "skip": skip},
                       cookie=cookie)
        block = data["questionList"]
        total = block["total"] if total is None else total
        rows = block["questions"]
        for q in rows:
            slugs.add(q["titleSlug"])
        skip += len(rows)
        if not rows or skip >= total:
            break
    return slugs


def user_from_slugs(username: str, slugs) -> UserSolved:
    """Build a FULL-mode user from an externally-supplied solved list.

    Used when the user (or their friend) exported their own solved slugs via the
    browser snippet, so no cookie is needed here. Public counts are still fetched
    for the summary display.
    """
    counts = fetch_counts(username)
    return UserSolved(username, "full", set(slugs), counts)


def fetch_user(username: str, cookie: str | None = None) -> UserSolved:
    """Fetch a user's solved data, using the cookie for a full list if given.

    NOTE: a cookie authenticates as its OWNER. `username` is used for the public
    counts display; when a cookie is supplied the solved set is the cookie
    owner's set (which should be `username`).
    """
    counts = fetch_counts(username)
    cookie = normalize_cookie(cookie)

    if cookie:
        solved = _fetch_solved_full(username, cookie)
        if not solved:
            # Cookie likely invalid/expired — fall back rather than silently lie.
            raise LeetCodeError(
                f"Cookie for '{username}' returned 0 solved problems — it is "
                "probably expired or wrong. Re-copy LEETCODE_SESSION, or run "
                "this user in public mode (no cookie).")
        return UserSolved(username, "full", solved, counts)

    # Public mode: best we can do is the last 20 accepted.
    data = graphql(_RECENT_QUERY, {"u": username, "l": 20})
    recent = {r["titleSlug"] for r in (data.get("recentAcSubmissionList") or [])}
    return UserSolved(username, "public", recent, counts)
