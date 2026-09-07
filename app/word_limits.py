"""Submission word-count limits vs rubric guidance."""
from __future__ import annotations

from app.config import WORD_COUNT_HARD_MAX, WORD_COUNT_MIN, WORD_COUNT_TARGET_MAX


def evaluate_word_count(count: int | None) -> dict:
    if count is None:
        return {
            "word_count": None,
            "level": "unknown",
            "message": "",
            "warn": False,
            "limits": _limits_dict(),
        }

    limits = _limits_dict()
    if count < WORD_COUNT_MIN:
        return {
            "word_count": count,
            "level": "short",
            "message": (
                f"Word count: {count:,} — below the required minimum of {WORD_COUNT_MIN:,} words."
            ),
            "warn": True,
            "limits": limits,
        }
    if count > WORD_COUNT_HARD_MAX:
        return {
            "word_count": count,
            "level": "over_limit",
            "message": (
                f"Word count: {count:,} — exceeds the maximum of {WORD_COUNT_HARD_MAX:,} words "
                f"(rubric: {WORD_COUNT_MIN:,}–{WORD_COUNT_TARGET_MAX:,}, not more than {WORD_COUNT_HARD_MAX:,})."
            ),
            "warn": True,
            "limits": limits,
        }
    if count > WORD_COUNT_TARGET_MAX:
        return {
            "word_count": count,
            "level": "above_target",
            "message": (
                f"Word count: {count:,} — above the target range "
                f"({WORD_COUNT_MIN:,}–{WORD_COUNT_TARGET_MAX:,}). Maximum allowed: {WORD_COUNT_HARD_MAX:,}."
            ),
            "warn": True,
            "limits": limits,
        }
    return {
        "word_count": count,
        "level": "ok",
        "message": (
            f"Word count: {count:,} (within target {WORD_COUNT_MIN:,}–{WORD_COUNT_TARGET_MAX:,})."
        ),
        "warn": False,
        "limits": limits,
    }


def _limits_dict() -> dict:
    return {
        "min": WORD_COUNT_MIN,
        "target_max": WORD_COUNT_TARGET_MAX,
        "hard_max": WORD_COUNT_HARD_MAX,
    }
