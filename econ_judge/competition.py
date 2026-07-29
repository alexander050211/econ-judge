"""Timed competition-round policy and CTFd challenge-state synchronisation.

The schedule is intentionally calculated from one timezone-aware start time,
rather than from background jobs or manual state changes. This keeps working
after a Render restart and makes CTFd's own challenge API enforce visibility.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


ROUND_1_CHALLENGE_IDS = frozenset({1, 2, 3, 4, 5, 6, 10, 11})
ROUND_2_CHALLENGE_IDS = frozenset({7, 8, 9, 12, 13, 14, 15})
ALL_CHALLENGE_IDS = ROUND_1_CHALLENGE_IDS | ROUND_2_CHALLENGE_IDS

ROUND_1_DURATION = timedelta(minutes=70)
BREAK_DURATION = timedelta(minutes=10)
ROUND_2_DURATION = timedelta(minutes=80)


@dataclass(frozen=True)
class CompetitionPhase:
    name: str
    visible_challenge_ids: frozenset[int]
    submissions_open: bool
    message: str = ""


def _competition_start() -> datetime | None:
    """Read the configured, timezone-aware ISO-8601 start time.

    Leaving the setting unset preserves normal development/review mode: every
    challenge is visible and submissions are open. A bare timestamp is rejected
    because a server-local timezone silently shifts a live contest.
    """
    raw = os.environ.get("ECON_JUDGE_COMPETITION_START", "").strip()
    if not raw:
        return None
    try:
        start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("ECON_JUDGE_COMPETITION_START must be timezone-aware ISO-8601") from exc
    if start.tzinfo is None:
        raise ValueError("ECON_JUDGE_COMPETITION_START must include a timezone offset")
    return start.astimezone(timezone.utc)


def current_phase(now: datetime | None = None) -> CompetitionPhase:
    """Return the current phase. ``now`` is injectable for deterministic tests."""
    try:
        start = _competition_start()
    except ValueError as exc:
        return CompetitionPhase(
            "misconfigured", frozenset(), False, f"Competition schedule error: {exc}"
        )
    if start is None:
        return CompetitionPhase("open", ALL_CHALLENGE_IDS, True)

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    round_1_end = start + ROUND_1_DURATION
    break_end = round_1_end + BREAK_DURATION
    round_2_end = break_end + ROUND_2_DURATION

    if now < start:
        return CompetitionPhase(
            "before", frozenset(), False, "The competition has not started yet."
        )
    if now < round_1_end:
        return CompetitionPhase("round1", ROUND_1_CHALLENGE_IDS, True)
    if now < break_end:
        return CompetitionPhase(
            "break",
            ROUND_1_CHALLENGE_IDS,
            False,
            "Submissions are closed during the break.",
        )
    if now < round_2_end:
        return CompetitionPhase("round2", ROUND_2_CHALLENGE_IDS, True)
    return CompetitionPhase(
        "finished",
        ALL_CHALLENGE_IDS,
        False,
        "The competition has finished; submissions are closed.",
    )


_last_synced_phase: str | None = None


def sync_challenge_states() -> CompetitionPhase:
    """Make CTFd's real visibility match the current scheduled phase.

    This runs before requests. It only writes on a phase transition, so it does
    not add database writes to ordinary challenge/API traffic. Importing CTFd
    here keeps this module testable without the application installed.
    """
    global _last_synced_phase

    phase = current_phase()
    if _last_synced_phase == phase.name:
        return phase

    from CTFd.models import Challenges, db

    changed = False
    for challenge in Challenges.query.filter(Challenges.id.in_(ALL_CHALLENGE_IDS)).all():
        wanted_state = (
            "visible" if challenge.id in phase.visible_challenge_ids else "hidden"
        )
        if challenge.state != wanted_state:
            challenge.state = wanted_state
            changed = True
    if changed:
        db.session.commit()
    _last_synced_phase = phase.name
    return phase
