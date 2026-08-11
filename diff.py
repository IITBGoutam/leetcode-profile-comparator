"""
diff.py — Set-difference logic over two users' solved sets.

Takes the shared catalog plus two `UserSolved` objects and produces the four
categories the spec asks for. Each category is a list of full `Problem` records
(so downstream filtering/sorting has tags + difficulty available).
"""

from __future__ import annotations

from fetch import Problem, UserSolved

CATEGORIES = ("friend-only", "me-only", "both", "neither")


def categorize(catalog: list[Problem],
               me: UserSolved,
               friend: UserSolved) -> dict[str, list[Problem]]:
    """Split the catalog into the four solved-set categories.

    friend-only : friend solved, I haven't   (the primary catch-up list)
    me-only     : I solved, friend hasn't
    both        : both solved
    neither     : neither solved
    """
    by_slug = {p.slug: p for p in catalog}
    me_set = me.solved_slugs
    fr_set = friend.solved_slugs

    result: dict[str, list[Problem]] = {c: [] for c in CATEGORIES}
    for slug, prob in by_slug.items():
        in_me = slug in me_set
        in_fr = slug in fr_set
        if in_fr and not in_me:
            result["friend-only"].append(prob)
        elif in_me and not in_fr:
            result["me-only"].append(prob)
        elif in_me and in_fr:
            result["both"].append(prob)
        else:
            result["neither"].append(prob)

    # Any solved slug not present in the catalog (rare: premium/renamed) still
    # counts toward "both/only" correctness above via set membership; it simply
    # can't be listed as a Problem row. That's acceptable for this tool.
    return result


def partial_warning(me: UserSolved, friend: UserSolved) -> str | None:
    """Human-readable caveat when either side is public-mode (approximate)."""
    weak = [u.username for u in (me, friend) if u.is_partial]
    if not weak:
        return None
    return (
        "Approximate results: no session cookie for "
        + ", ".join(weak)
        + " — only their last-20 accepted problems are known, so 'solved' sets "
          "for them are incomplete. Counts (below) are still exact."
    )
