"""
filters.py — Filtering and sorting applied to any category's problem list.
"""

from __future__ import annotations

from fetch import Problem

_DIFF_ORDER = {"Easy": 0, "Medium": 1, "Hard": 2}


def apply_filters(problems: list[Problem],
                  difficulty: str | None = None,
                  tags: list[str] | None = None,
                  search: str | None = None,
                  match_all_tags: bool = False,
                  rating_min: int | None = None,
                  rating_max: int | None = None,
                  rating_mode: str = "all") -> list[Problem]:
    """Filter by difficulty, topic tag(s), title search, and zerotrac rating.

    difficulty : "Easy" | "Medium" | "Hard" (case-insensitive), or None.
    tags       : list of tag slugs (e.g. ["dynamic-programming", "graph"]).
    match_all_tags : if True a problem must carry ALL given tags, else ANY.
    search     : case-insensitive substring matched against the title.
    rating_min / rating_max : inclusive numeric range on the zerotrac rating
        (e.g. 1759..2434). A range implies "rated", so unrated problems
        (rating is None) are excluded whenever a bound is given.
    rating_mode : "all" (no rating-type filter), "rated" (only problems that
        have a rating), or "unrated" (only problems with no rating). A null
        rating can't live inside a numeric range, so unrated is its own bucket.
    """
    out = problems

    if difficulty:
        d = difficulty.strip().capitalize()
        out = [p for p in out if p.difficulty == d]

    if tags:
        wanted = {t.strip().lower() for t in tags if t.strip()}
        if wanted:
            if match_all_tags:
                out = [p for p in out if wanted <= set(p.tags)]
            else:
                out = [p for p in out if wanted & set(p.tags)]

    if search:
        needle = search.strip().lower()
        out = [p for p in out if needle in p.title.lower()]

    # ---- rating ---- #
    if rating_mode == "unrated":
        # Only classic/non-contest problems; range bounds don't apply.
        out = [p for p in out if p.rating is None]
    else:
        if rating_mode == "rated" or rating_min is not None or rating_max is not None:
            out = [p for p in out if p.rating is not None]
        if rating_min is not None:
            out = [p for p in out if p.rating >= rating_min]
        if rating_max is not None:
            out = [p for p in out if p.rating <= rating_max]

    return out


def sort_problems(problems: list[Problem], key: str = "difficulty",
                  reverse: bool = False) -> list[Problem]:
    """Sort by 'title', 'difficulty', 'acrate', or 'rating'."""
    if key == "title":
        keyfn = lambda p: p.title.lower()
    elif key == "acrate":
        keyfn = lambda p: p.ac_rate
    elif key == "rating":
        # Unrated (None) is treated as the lowest value (appears first when
        # ascending, last when descending); ties broken by title.
        keyfn = lambda p: (p.rating if p.rating is not None else -1,
                           p.title.lower())
    else:  # difficulty (Easy < Medium < Hard), then title
        keyfn = lambda p: (_DIFF_ORDER.get(p.difficulty, 99), p.title.lower())
    return sorted(problems, key=keyfn, reverse=reverse)
