"""
tests/test_watchlist.py — CineLog

Tests for the watchlist service. These mirror the structure of
tests/test_collection.py (see CONTRIBUTING.md: happy path, duplicate/conflict,
nonexistent ID) and add a few watchlist-specific cases.
"""

import pytest
from app import create_app, db
from models import User, Film, WatchlistEntry
from services.watchlist_service import (
    add_to_watchlist,
    get_watchlist,
    AlreadyInWatchlistError,
)
from services.collection_service import FilmNotFoundError


@pytest.fixture
def app():
    """Create an isolated test app with an in-memory database."""
    app = create_app(config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def sample_user(app):
    """A user to use in tests."""
    with app.app_context():
        user = User(username="testuser", email="test@example.com")
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def sample_film(app):
    """A film to use in tests."""
    with app.app_context():
        film = Film(title="Paddington 2", year=2017, genre="Comedy")
        db.session.add(film)
        db.session.commit()
        return film.id


# ── Basic add (happy path) ───────────────────────────────────────────────────

def test_add_to_watchlist_creates_entry(app, sample_user, sample_film):
    """Adding a valid film should create a WatchlistEntry in the database."""
    with app.app_context():
        entry = add_to_watchlist(user_id=sample_user, film_id=sample_film)

        assert entry is not None
        assert entry.user_id == sample_user
        assert entry.film_id == sample_film

        in_db = WatchlistEntry.query.filter_by(
            user_id=sample_user, film_id=sample_film
        ).first()
        assert in_db is not None


# ── Default visibility (Comment 4) ───────────────────────────────────────────

def test_add_to_watchlist_defaults_to_private(app, sample_user, sample_film):
    """
    A newly added entry defaults to private (public=False). This locks in the
    design decision documented in pr-response.md, Comment 4.
    """
    with app.app_context():
        entry = add_to_watchlist(user_id=sample_user, film_id=sample_film)
        assert entry.public is False


def test_add_to_watchlist_respects_public_flag(app, sample_user, sample_film):
    """Callers can opt into public visibility explicitly (visibility toggle)."""
    with app.app_context():
        entry = add_to_watchlist(user_id=sample_user, film_id=sample_film, public=True)
        assert entry.public is True


# ── Deduplication (Comment 2) ────────────────────────────────────────────────

def test_add_to_watchlist_duplicate_raises(app, sample_user, sample_film):
    """
    Adding the same film twice should raise AlreadyInWatchlistError,
    not silently create a duplicate entry.
    """
    with app.app_context():
        add_to_watchlist(user_id=sample_user, film_id=sample_film)

        with pytest.raises(AlreadyInWatchlistError):
            add_to_watchlist(user_id=sample_user, film_id=sample_film)

        count = WatchlistEntry.query.filter_by(
            user_id=sample_user, film_id=sample_film
        ).count()
        assert count == 1


# ── Nonexistent film (Comment 3) ─────────────────────────────────────────────

def test_add_to_watchlist_nonexistent_film_raises(app, sample_user):
    """
    Adding a film_id that doesn't exist in the database should raise
    FilmNotFoundError, not a database integrity error.

    Modeled on test_add_to_collection_nonexistent_film_raises.
    """
    with app.app_context():
        fake_film_id = "00000000-0000-0000-0000-000000000000"

        with pytest.raises(FilmNotFoundError):
            add_to_watchlist(user_id=sample_user, film_id=fake_film_id)


# ── get_watchlist sort order (Comment 5) ─────────────────────────────────────

def test_get_watchlist_returns_newest_first(app, sample_user):
    """
    get_watchlist() should return films sorted by date_added descending
    (most recently added first), consistent with get_collection().
    """
    with app.app_context():
        from datetime import datetime, timezone, timedelta

        film_a = Film(title="Alien", year=1979, genre="Horror")
        film_b = Film(title="Blade Runner", year=1982, genre="Sci-Fi")
        db.session.add_all([film_a, film_b])
        db.session.commit()

        earlier = datetime.now(timezone.utc) - timedelta(days=5)
        later = datetime.now(timezone.utc)

        entry_a = WatchlistEntry(user_id=sample_user, film_id=film_a.id, date_added=earlier)
        entry_b = WatchlistEntry(user_id=sample_user, film_id=film_b.id, date_added=later)
        db.session.add_all([entry_a, entry_b])
        db.session.commit()

        watchlist = get_watchlist(sample_user)
        titles = [f["title"] for f in watchlist]

        # Blade Runner was added later, so it should come first.
        assert titles[0] == "Blade Runner"
        assert titles[1] == "Alien"


# ── Extra edge case (stretch): dedup is scoped per user ──────────────────────

def test_two_users_can_watchlist_same_film(app, sample_film):
    """
    The uniqueness guarantee is per (user, film), not global to the film.
    Two different users must both be able to add the same film — otherwise the
    dedup logic would leak one user's watchlist state into another's. I chose
    this case because it is the failure mode of a too-broad unique constraint
    (e.g. one accidentally placed on film_id alone).
    """
    with app.app_context():
        user_one = User(username="ada", email="ada@example.com")
        user_two = User(username="grace", email="grace@example.com")
        db.session.add_all([user_one, user_two])
        db.session.commit()
        uid1, uid2 = user_one.id, user_two.id

        add_to_watchlist(user_id=uid1, film_id=sample_film)
        # Must not raise: this is a different user.
        add_to_watchlist(user_id=uid2, film_id=sample_film)

        assert WatchlistEntry.query.filter_by(film_id=sample_film).count() == 2
