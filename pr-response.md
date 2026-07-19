# PR Response Doc — CineLog Watchlist Feature

Thanks for the thorough review, @dev-lead. Below is a point-by-point response to
all six comments, including my reasoning for the two design decisions (Comments 4
and 5) and how I resolved the rebase conflict (Comment 6).

---

## AI Usage

I used an AI assistant for **orientation and hygiene**, not for the design
decisions:

- **Orientation:** summarizing `models.py`, `services/collection_service.py`,
  and `tests/test_collection.py` so I understood the existing `verb_to_noun`
  naming, how `add_to_collection()` handles deduplication, and the three-case
  test pattern (happy path / duplicate / nonexistent) before touching the
  watchlist code.
- **Hygiene:** sanity-checking that my rewritten commit messages matched the
  Conventional Commits table in `CONTRIBUTING.md`.
- **Stress-testing (devil's advocate):** after I wrote my Comment 4 and Comment 5
  positions myself, I gave the drafts to an AI playing a skeptical maintainer and
  asked: *"What's the single strongest counterargument, and what tradeoff am I not
  acknowledging?"* It surfaced three points I judged real and revised for:
  1. **C4:** I was letting `public=False` read like a privacy *guarantee* when the
     flag is unenforced — so I added an explicit "this default is inert until
     enforcement lands; it is not protection today" paragraph and listed auth +
     enforcement as follow-ups.
  2. **C4:** I hadn't acknowledged that private-by-default makes the *more* sensitive
     list conservative while `CollectionEntry` stays unconditionally public over the
     same open endpoint — I now name that inconsistency instead of hiding it, and I
     tempered my "retroactive mass-exposure" framing by admitting the symmetric
     "retroactive mass-hide" cost.
  3. **C5:** I was over-crediting "consistency with get_collection." I reframed so
     the primary reason is "alphabetical is indefensible; newest-first is a
     reasonable interim," demoted consistency to a tie-breaker, and compared
     newest-first against **oldest-first** head-to-head (the real queue alternative).

  The positions and tradeoffs are my own, grounded in CineLog's actual code
  (unauthenticated GET endpoint, unenforced `public` flag, sibling `get_collection()`
  ordering). Where I disagreed with the AI's framing I kept my own — e.g. it argued
  `public=False` could be "worse than `public=True`" by lying about state; I rejected
  that because irreversibility of exposure still outweighs a recoverable mass-hide.

Everything the AI surfaced was verified against the code — e.g. it initially
described `entry.film` as working on the watchlist, but reading the models showed
`Film` had no `watchlist_entries` relationship, which turned out to be a real
latent bug (see Comment 1 notes).

---

## Comment 1 — Rename `save_to_watchlist()` → `add_to_watchlist()`

**What I did:**
- Renamed `save_to_watchlist()` to `add_to_watchlist()` in
  `services/watchlist_service.py` to match the project's `verb_to_noun` pattern
  (`add_to_collection`, `remove_from_collection`, `get_collection`).
- Updated the one call site in `routes/watchlist/watchlist.py` and its import.
- While in the file I also gave the service the full sibling API so the naming
  is consistent across the board: `add_to_watchlist`, `remove_from_watchlist`,
  `get_watchlist`.

**How I verified:**
- `grep -rn "save_to_watchlist"` returns no remaining references.
- App boots and all three `/watchlist/...` routes register.
- Full test suite passes (`pytest tests/`).

**Note (latent bug found):** the originally-submitted `get_watchlist()` relied on
`entry.film`, but `Film` only defined a `collection_entries` relationship — there
was no backref to `WatchlistEntry`, so `entry.film` would raise `AttributeError`
at runtime (no test exercised that path yet, which is why it slipped through). I
added `watchlist_entries = db.relationship("WatchlistEntry", backref="film", ...)`
to `Film`, mirroring `collection_entries`, in the baseline feature commit. The
`get_watchlist` sort-order test (Comment 5) now exercises this path.

---

## Comment 2 — Deduplication

**What I did:**
Followed the exact pattern `add_to_collection()` already uses, so the two
services behave identically:
- Added a `UniqueConstraint("user_id", "film_id", name="unique_user_film_watchlist")`
  to `WatchlistEntry` (the DB-level guarantee, matching
  `unique_user_film_collection` on `CollectionEntry`).
- Added an application-level pre-check in `add_to_watchlist()` that raises a new
  `AlreadyInWatchlistError` before insert — mirroring `AlreadyInCollectionError`.
- Mapped that exception to **HTTP 409** in the route, matching the collection
  endpoint's contract.

**How I verified:**
- `test_add_to_watchlist_duplicate_raises` adds the same film twice, asserts
  `AlreadyInWatchlistError`, and confirms only one row exists.
- `test_two_users_can_watchlist_same_film` confirms the constraint is scoped to
  `(user_id, film_id)`, not `film_id` alone.

---

## Comment 3 — Missing test (nonexistent `film_id`)

**What I did:**
Added `tests/test_watchlist.py`, modeled on `tests/test_collection.py`. It covers
the three cases `CONTRIBUTING.md` requires plus watchlist-specific ones:
- happy path (`test_add_to_watchlist_creates_entry`)
- duplicate/conflict (`test_add_to_watchlist_duplicate_raises`)
- **nonexistent film** (`test_add_to_watchlist_nonexistent_film_raises`) — the
  case you asked for: a `film_id` not in the DB raises `FilmNotFoundError`, not a
  DB integrity error.
- sort order, default visibility, the visibility toggle, `remove_from_watchlist`,
  and the per-user dedup edge case.

**How I verified:** `pytest tests/` → 13 passed (4 existing collection tests + 9
new watchlist tests).

---

## Comment 4 — Default visibility

**My position:** Watchlist entries should default to **private** (`public=False`).
I changed the model default from `True` to `False` and added an explicit `public`
parameter to `add_to_watchlist()` / the `/add` endpoint so callers can opt into
sharing.

**Reasoning (grounded in CineLog's current code, not a generic privacy stance):**

1. **The default only matters for *when enforcement lands* — and I'm not pretending
   otherwise.** Today the `public` flag is inert: `get_watchlist()` returns *every*
   entry regardless of it, and `GET /watchlist/<user_id>` has **no authentication**,
   so every watchlist is fully exposed no matter what the default is. So `public=False`
   is **not** a privacy guarantee right now, and it shouldn't be mistaken for one — the
   actual privacy work is the enforcement + auth, not this default. What the default
   *does* decide is the value sitting on every existing row the day someone adds
   filtering. Given the choice of which state to pre-write, `False` makes that future
   feature an opt-in rather than a retroactive flip on data users never marked.

2. **Asymmetric reversibility.** A private-by-default entry a user later shares is
   harmless. A public-by-default entry is a one-way leak — once the list has been
   fetched over that open endpoint, "making it private later" doesn't recall it. This
   is why I break the tie toward `False`: both directions surprise *some* users when
   enforcement lands (see tradeoff), but only one direction is irreversible.

3. **A watchlist is future intent, and it's a new surface.** Collections have *no*
   visibility field, so `WatchlistEntry` is the first per-list privacy control in the
   app — whatever we pick sets the precedent, and "films I *want* to watch" is
   arguably more revealing than watch history.

**A gap I want to name (not hide):** point 3 creates an inconsistency — with
`WatchlistEntry` private-by-default, the *more* sensitive list is conservative while
`CollectionEntry` (watch history) stays **unconditionally public** over the same
unauthenticated endpoint. That's the wrong protection ordering. I'm not resolving it
in this PR (collection visibility is out of scope), but it argues that the *real* fix
is endpoint-level auth + `public` enforcement, and I've listed both as follow-ups.
Until then, `public=False` is the safer value to record, not a solved problem.

**Tradeoff acknowledged:** CineLog's README leads with "community film tracking," so
private-by-default has a real cost: when filtering ships, it's a *retroactive
mass-hide* — every existing watchlist silently drops out of any community view users
may have come to expect, gutting the headline feature. And defaults are sticky: most
users won't toggle, so "private by default" likely means most watchlists stay
invisible. I still take that trade over public-by-default, because a mass-hide is
recoverable (users/product can opt back into sharing intentionally — a per-user
"public" setting or a share action) whereas a mass-exposure is not. The new `public`
parameter keeps one-tap sharing available for a UI that wants it.

---

## Comment 5 — Sort order

**My position:** I agree with you — I changed `get_watchlist()` from alphabetical
(`Film.title.asc()`) to **date added, newest first** (`date_added.desc()`).

**Reasoning (honest version — I'm not going to over-sell "consistency"):**

1. **The primary reason is that alphabetical is the worst of the options here**, not
   that newest-first is provably best. `Film.title.asc()` sorts by an attribute of the
   *film* that has nothing to do with the user's relationship to it; it can't answer
   *any* watchlist question ("what did I just add?", "what have I been meaning to watch
   longest?") and there was no parameter to change it. Removing it is the clear win.

2. **Between the defensible orders, newest-first is a reasonable interim pick.** It
   matches the most common expectation (the film you just saved is top-of-mind and
   should be findable at the top) and it happens to match `get_collection()`, so a
   client renders both lists the same way. I'm treating consistency as a *tie-breaker*,
   not the main argument — because, as below, a watchlist isn't semantically the same
   as a collection.

**Engagement with the reviewer's point (and the counterargument to my own choice):**
Your "most users want to see what they added recently" claim is right for the common
case. But the honest tension is that a *collection* is a **log** (recency = "what I
watched"), while a *watchlist* is a **backlog/queue you pick from**, and for a queue
newest-first actively **buries the oldest items** — the ones that have waited longest
and are arguably most due to be watched. So the strongest alternative isn't
alphabetical, it's **oldest-first (`date_added.asc()`)**, which serves the
work-through-your-backlog use case. I chose newest-first over oldest-first as the
interim default because (a) it matches the likelier day-to-day expectation of "where's
the thing I just added," and (b) surfacing the oldest, longest-ignored items first can
feel like a guilt-list. But I hold this loosely: the genuinely correct answer is an
optional `?sort=` param (`date_added` | `title`, and plausibly asc/desc), which I've
scoped out of this PR as a follow-up. If you'd rather default to oldest-first, or want
the sort param folded in now, I'm happy to — this is a placeholder, not a hill.

---

## Comment 6 — Rebase onto updated `main`

**What conflicted:**
The `refactor: migrate film IDs from integer to UUID` commit on `main` rewrote
`models.py`: it changed `Film.id` and `CollectionEntry.film_id` from `Integer` to
`String(36)` UUIDs and, in doing so, rewrote the block where my `WatchlistEntry`
lived. My branch still defined `WatchlistEntry.film_id` as `Integer` and my
watchlist code/docstrings assumed integer IDs. Rebasing surfaced a conflict in
`models.py` (my modified `WatchlistEntry` region vs. `main`'s rewrite of that
region) — a genuine "your branch is stale" conflict, not a mechanical one.

**How I resolved it:**
- Rebased with `git rebase main` (no merge commit).
- Took `main`'s UUID version of `Film` and `CollectionEntry` wholesale.
- Re-added `WatchlistEntry` on top of the UUID models with
  `film_id = db.Column(db.String(36), db.ForeignKey("film.id"), ...)`, keeping my
  dedup `UniqueConstraint` and `public=False` default, and re-added the
  `Film.watchlist_entries` relationship.
- Updated `watchlist_service.py` docstrings from "film_id (int)" to UUID/`str`.
- The service logic (`db.session.get(Film, film_id)`, `filter_by`) is ID-type
  agnostic, and the tests reference the fixture's generated id rather than a
  hard-coded integer, so no test logic needed to change.

**How I verified no conflict remains:**
- `git status` clean; `git log --oneline` shows a linear history with **no merge
  commits** (`git log --merges` on the branch range is empty).
- `git rev-list --count main..feature/watchlist` matches the number of clean
  commits I authored.
- `pytest tests/` → all tests pass against the UUID models.
- App boots and all watchlist routes register.

---

## Commit History

History was rewritten (`git rebase -i`) into conventional commits, one logical
change each, then rebased onto `main` — linear, no merge commits. Each review
response is its own commit so the review conversation is legible in the log.

Actual `git log --oneline origin/main..HEAD` (the terminal equivalent of the
required screenshot — 9 commits, all conventional, 0 merges):

```
4c66d56 docs: add PR response doc for watchlist review
d51b75b feat: add remove_from_watchlist endpoint
8f3869a feat: order watchlist by date added, newest first
1815348 feat: default watchlist visibility to private
43f644c test: add watchlist service tests
d6646e1 feat: deduplicate watchlist entries
12bd8c2 refactor: rename save_to_watchlist to add_to_watchlist
5350693 feat: add watchlist model, service, and endpoints
f6ec53a refactor: use db.session.get for film lookups in collection service
```

Mapped to the review (bottom → top): baseline feature → **C1** rename → **C2**
dedup → **C3** tests → **C4** private default → **C5** newest-first sort →
stretch remove endpoint → docs. (`refactor: use db.session.get` is pre-existing
branch work, unrelated to the review.)

The int→UUID reconciliation (**Comment 6**) is folded into the branch's base
rather than shown as a separate commit: the branch is rebased directly on the
UUID-refactored `main`, and `WatchlistEntry.film_id` is `String(36)` throughout.

(The `docs:` line's SHA changes when this file is committed — embedding a log in
the same file it documents is inherently one commit behind. The eight lower SHAs
and all nine messages are final.)

---

## PR Description

**What it does:** Adds a watchlist so users can save films they want to watch
later. Introduces the `WatchlistEntry` model (unique per user+film), a
`watchlist_service` with `add_to_watchlist`, `remove_from_watchlist`, and
`get_watchlist`, and REST endpoints under `/watchlist`.

**Design decisions:**
- **Visibility defaults to private** (`public=False`), with an explicit `public`
  parameter for opting in. See Comment 4 for the reasoning (unenforced flag +
  unauthenticated GET endpoint = private is the only safe default to record).
- **Watchlist is ordered newest-first** to match `get_collection()`. See
  Comment 5. A future `?sort=` param is the intended way to support alphabetical
  ordering for large lists.
- Deduplication mirrors the collection service exactly (DB unique constraint +
  application-level `AlreadyInWatchlistError` → HTTP 409).

**Follow-ups (out of scope for this PR):**
- Enforce the `public` flag in `get_watchlist()` and add auth to the GET endpoint.
- Optional `?sort=date_added|title` parameter on the view endpoint.

**How to test end to end:**
```bash
pip install -r requirements.txt
pytest tests/            # 13 passing

python app.py            # starts on http://localhost:5000
# Seed a user + film in a shell, then:
#   POST   /watchlist/<user_id>/add     {"film_id": "<uuid>"}            -> 201, public=false
#   POST   /watchlist/<user_id>/add     {"film_id": "<uuid>"}            -> 409 (duplicate)
#   POST   /watchlist/<user_id>/add     {"film_id": "<uuid>", "public": true} -> 201, public=true
#   POST   /watchlist/<user_id>/add     {"film_id": "does-not-exist"}    -> 404
#   GET    /watchlist/<user_id>                                          -> newest-first list
#   DELETE /watchlist/<user_id>/remove  {"film_id": "<uuid>"}            -> 200
#   DELETE /watchlist/<user_id>/remove  {"film_id": "<uuid>"}            -> 404 (already gone)
```

## Stretch features included
- **`remove_from_watchlist(user_id, film_id)`** + `DELETE /watchlist/<user_id>/remove`,
  following `remove_from_collection`; raises `NotInWatchlistError` → HTTP 404.
  Tested by `test_remove_from_watchlist_deletes_entry` and
  `test_remove_from_watchlist_not_present_raises`.
- **Extra edge-case test** `test_two_users_can_watchlist_same_film`: I chose this
  because it's the failure mode of a too-broad unique constraint (one placed on
  `film_id` alone would let one user's watchlist block another's). It proves the
  dedup guarantee is correctly scoped to `(user_id, film_id)`.
- **Visibility toggle:** the `public` parameter on `add_to_watchlist()` / the
  `/add` endpoint (see Comment 4), tested by
  `test_add_to_watchlist_respects_public_flag` and
  `test_add_to_watchlist_defaults_to_private`.
