# LeetCode Profile Comparator

Compare two LeetCode profiles and list the problems your friend solved that you
haven't — filterable by difficulty, topic tag, title search, and **zerotrac
contest rating** — so you can prioritize catch-up practice.

```
python compare.py --me alice --friend bob --diff friend-only --difficulty Hard
```

## ⚠️ Read this first: what LeetCode actually lets you fetch

LeetCode has **no official API**, and its unofficial GraphQL endpoint does **not
expose the full list of problems a user has solved** to the public. Concretely:

| Data | Public? |
|------|---------|
| Solved **counts** per difficulty (Easy/Med/Hard) | ✅ yes, per username |
| Full problem catalog (title, difficulty, tags, AC%) | ✅ yes |
| The **complete list** of problems a user solved | ❌ only with **that user's** login cookie |
| A user's **20 most recent** accepted problems | ✅ but hard-capped at 20 |

So each user is fetched in one of two modes:

- **full** — you supplied that user's `LEETCODE_SESSION` cookie → exact, complete
  solved set → the per-problem diff is **accurate**.
- **public** — no cookie → only counts + last-20 solved → the diff is
  **approximate** (the tool clearly labels this and still shows exact counts).

**To get a true "everything my friend solved that I haven't" list, you each need
to supply your own cookie once.** Getting yours is trivial; your friend has to
cooperate one time. See [Cookies](#getting-the-full-accurate-list-cookies).

## Two ways to use it

- **Web UI** (recommended): `python server.py` → opens a page with a click-to-sort
  table, category chips, difficulty badges, and optional cookie fields.
- **CLI**: `python compare.py ...` for terminal use and scripting.

Both share the exact same backend (`fetch`/`diff`/`filters`/`cache`).

## Setup

Requires **Python 3.10+**. No third-party packages — standard library only.

### Web UI
<img width="1572" height="898" alt="image" src="https://github.com/user-attachments/assets/9535d52b-f0ae-4048-bf06-d90d2c2bebf3" />

```
python server.py                 # opens http://localhost:8010 in your browser
python server.py --port 9000     # custom port (e.g. to avoid a clash)
python server.py --no-open       # don't auto-open the browser
```

Enter both usernames, optionally expand **Full accurate mode** to paste each
person's `LEETCODE_SESSION` (for an exact diff), then **Compare**. Set a
**Min/Max rating** window (and a rated / unrated toggle) to narrow to a precise
difficulty band. Click a category chip to switch between friend-only / me-only /
both / neither, and click any column header (incl. **Rating**) to sort. Cookies
are POSTed to the local server only — never put in a URL and never cached.

**You only fill this in once.** With *Remember on this computer* ticked (it is by
default), a successful comparison saves your usernames and cookies locally, and
every later visit comes back pre-filled — start the app, press **Compare**. See
[Staying signed in](#staying-signed-in).

### CLI

```
python compare.py --me <you> --friend <friend>
```

Or save usernames (and optionally cookies) so you don't retype them:

```
copy config.example.json config.json    # then edit config.json
python compare.py                        # reads --me/--friend from config.json
```

Easier: run the web UI once with **Remember on this computer** ticked — it writes
`config.json` for you (cookies encrypted), and the CLI then picks it up.

## Staying signed in

Nothing here should have to be typed twice. After a comparison succeeds, the web
UI saves what you entered to `config.json` next to the code, and reloads it on
every later visit — usernames pre-filled, cookie fields showing
`✓ saved (…9f2a) — leave blank to reuse it`. The **Remember on this computer**
checkbox controls this, and **Forget saved details** wipes it.

- **The cookie never goes into the browser.** It is stored server-side; the page
  is only ever told *that* one is saved, plus its last four characters.
- **On Windows it is encrypted with DPAPI**, tied to your Windows account: the
  stored value is useless on another machine or under another user. You'll see it
  as `"me_cookie_enc": "dpapi:…"`. An older hand-written `config.json` with a
  plaintext cookie is upgraded automatically the first time the UI saves.
- **On Linux/macOS there is no dependency-free equivalent**, so the cookie is
  stored as `plain:…` with the file mode set to `0600`. Nothing else changes.
- `config.json` is gitignored either way.

### An expired cookie can't break a working setup

Session cookies die after about two weeks. That no longer matters much: every
profile you fetch is saved to `cache/users/`, and **that snapshot is what the
comparison runs on**. If a refresh fails — expired cookie, no internet — the
comparison still renders from the saved list and shows an amber note instead of
an error. Each profile displays *Saved list · 3 days ago* with an **Update now**
button when you do want fresh data (paste a new cookie first if it asks).

## Getting the full, accurate list

There are two ways to give a person an exact solved set. **Do not share session
cookies with each other** — a `LEETCODE_SESSION` cookie is full account access.

### Best for your friend: the share export (no cookie) ✅

Your friend runs a tiny snippet in *their own* browser. Because it runs on
leetcode.com, the browser attaches their login automatically — so it reads their
own solved list without them ever copying a cookie. It downloads a small file
(just problem slugs — nothing sensitive) that they send you, and you import it.

1. Start the web UI (`python server.py`) and open **http://localhost:8010/share**.
2. Send that page to your friend. Simplest: send them the file
   **`web/share.html`** directly (it's fully self-contained — they double-click
   to open it). *(A localhost link won't work for a remote friend, but the HTML
   file will.)*
3. Your friend follows the 3 steps (drag the bookmarklet, or paste the console
   snippet) and gets `leetcode-solved-<name>.json`.
4. They send you that file. In the web UI, open **Full accurate mode → A · Import
   a shared export** and pick their file. (CLI: `--friend-solved theirfile.json`.)

The same works for your own list, if you'd rather not use a cookie — and when you
run the bookmarklet **on the machine that's running the comparator**, it skips the
file entirely and pushes the list straight into it: click the bookmark on a
LeetCode tab, switch back, press Compare. Chrome may first ask whether
leetcode.com can reach devices on your local network (that's the page talking to
your own comparator) — choose **Allow**. Decline it and you simply get the
downloadable file instead.

### Quickest for yourself: your session cookie

The cookie value comes from your own logged-in browser session:

1. Log in to leetcode.com.
2. Open DevTools (F12) → **Application** → **Cookies** → `https://leetcode.com`.
3. Copy the value of the **`LEETCODE_SESSION`** cookie.

**Where do you put it?** Any of these (highest precedence first):

| Where | How | Best when |
|-------|-----|-----------|
| **Web UI field** | Full accurate mode → B · paste into "Your cookie" | almost always — tick *Remember* and you never paste it again |
| **Environment variable** | `setx LEETCODE_COOKIE_ME "LEETCODE_SESSION=…"` (PowerShell) | shared machines; keeps it out of any file |
| **`config.json`** | written for you by the UI as `me_cookie_enc` (encrypted) | the default once you've ticked *Remember* |
| **CLI flag** | `--me-cookie "LEETCODE_SESSION=…"` | scripting |

The CLI reads the same saved `config.json`, so a cookie saved from the web UI
works for `python compare.py` too.

You can paste the bare token or the whole `LEETCODE_SESSION=…` string — either
works. Cookies are used only for requests to leetcode.com and are **never written
to the cache**.

## Usage

```
python compare.py [options]

  --me NAME               your LeetCode username
  --friend NAME           friend's LeetCode username
  --me-cookie VAL         your LEETCODE_SESSION cookie (for a full list)
  --friend-cookie VAL     friend's cookie
  --me-solved FILE        import your solved list from a share-export .json
  --friend-solved FILE    import friend's solved list (username read from file)
  --diff CATEGORY         friend-only (default) | me-only | both | neither
  --difficulty LEVEL      Easy | Medium | Hard
  --tag SLUG              topic tag slug; repeatable (e.g. --tag graph --tag dynamic-programming)
  --all-tags              require ALL given tags (default: match any)
  --search TEXT           case-insensitive title substring
  --min-rating N          only problems with zerotrac rating >= N (e.g. 1759)
  --max-rating N          only problems with zerotrac rating <= N (e.g. 2434)
  --rating MODE           rated (contest problems only) | unrated (only unrated)
  --sort KEY              difficulty (default) | title | acrate | rating
  --desc                  sort descending
  --limit N               max rows (0 = all; default 50)
  --refresh               ignore cache, re-fetch from LeetCode
  --list-tags             print all valid tag slugs with counts, then exit
```

### The four categories (`--diff`)

- `friend-only` — friend solved, you haven't  ← **the catch-up list**
- `me-only` — you solved, friend hasn't
- `both` — both solved
- `neither` — neither solved (good for "let's both grind these")

### Examples

```powershell
# The Hard problems your friend has done that you haven't:
python compare.py --me alice --friend bob --diff friend-only --difficulty Hard

# Same, but only DP or graph problems, hardest-to-solve first:
python compare.py --me alice --friend bob --diff friend-only ^
    --tag dynamic-programming --tag graph --sort acrate

# Find valid tag slugs:
python compare.py --list-tags

# Problems neither of you has touched, containing "tree" in the title:
python compare.py --me alice --friend bob --diff neither --search tree

# Catch-up problems in a precise difficulty window (Codeforces-style rating),
# hardest first — great for graded revision:
python compare.py --me alice --friend bob --diff friend-only ^
    --min-rating 1759 --max-rating 2434 --sort rating --desc

# Only classic non-contest problems (the ones zerotrac can't rate):
python compare.py --me alice --friend bob --diff neither --rating unrated
```

## Problem ratings (zerotrac)

LeetCode only labels problems **Easy / Medium / Hard** — three coarse buckets
that hide huge variation. This tool joins in [zerotrac's community rating
dataset][zt], which assigns each *contest* problem a single numeric difficulty
score (~1000–3500, Elo/Codeforces-style, fit from real contest solve data). That
lets you filter to a precise window like **1759–2434** and sort by true
difficulty instead of a three-way bucket.

- **Join key** is the problem slug, so matching is exact (no fuzzy titles).
- **Only contest problems are rated.** Classic problems (Two Sum, LIS, …) have no
  rating anywhere and show as **unrated** (`—`). Use `--rating unrated` to list
  just those, or `--rating rated` to exclude them. A numeric range implicitly
  excludes unrated problems (a null can't sit inside a range).
- The dataset is cached at `cache/ratings.json` and refreshed daily (it updates
  after each weekly/biweekly contest); force a pull with `--refresh`.

[zt]: https://github.com/zerotrac/leetcode_problem_rating

## How it's organized

| File | Responsibility |
|------|----------------|
| `fetch.py` | All LeetCode GraphQL access (the only network code). Isolated so an endpoint change touches one file. |
| `diff.py` | Splits the catalog into the four solved-set categories. |
| `filters.py` | Difficulty / tag / search / rating filtering and sorting. |
| `cache.py` | JSON file cache under `./cache/` so repeat runs are instant. |
| `compare.py` | CLI wiring, config/cookie resolution, table rendering. |

## Caching

- `cache/catalog.json` — the problem list (refreshed weekly, or with `--refresh`).
- `cache/ratings.json` — zerotrac's `{slug: rating}` map (refreshed daily, or with `--refresh`).
- `cache/users/<name>.json` — a user's solved set (refreshed every 6h, or `--refresh`).

Delete the `cache/` folder to reset. Cookies are never cached.

## Limitations

- **Public mode is approximate** for the per-problem diff (last-20 only). Use
  cookies for accuracy. Counts are always exact.
- A private profile won't return counts (the tool reports this).
- Company tags are premium-gated and intentionally not supported.
- The endpoint is unofficial and can change without notice.
- **Ratings cover contest problems only** (~63% of the catalog); everything else
  is unrated. If GitHub is unreachable, a cached rating map is used (or, if none
  exists, all problems show as unrated) — the rest of the tool still works.

## Credits

Problem ratings come from [**zerotrac/leetcode_problem_rating**][zt], a
community-maintained dataset. This project just fetches, caches, and joins that
file by slug — all rating credit belongs to zerotrac and contributors.
