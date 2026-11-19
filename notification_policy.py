"""Strict P93 notification gate. Unknown eligibility never generates Telegram alerts.

Only postings with an explicitly matched P93 minimum at or below the configured
limit and a recognizable general/graduate associate-degree requirement qualify.
This is a notification filter, not a guarantee of employment eligibility.
"""
from __future__ import annotations
import re


def _row_matches(assessment: dict, config: dict) -> bool:
    if assessment.get("status") == "excluded":
        return False
    if assessment.get("score_ambiguous") or not assessment.get("evidence_complete", False):
        return False
    if assessment.get("exam_year_conflict"):
        return False
    conditions = assessment.get("score_conditions") or []
    if len(conditions) != 1:
        return False
    condition = conditions[0]
    candidate = config["candidate"]
    limit = float(config["search_settings"].get("notification_max_required_p93_score", 70))
    if condition.get("score_type") != "P93" or candidate.get("kpss_score_type") != "P93":
        return False
    required = float(condition["minimum_score"])
    if required > limit or required > float(candidate["kpss_score"]):
        return False
    return bool(assessment.get("general_associate_code")
                or assessment.get("department_mentioned")
                or assessment.get("general_associate_requirement")
                or assessment.get("minimum_high_school_open"))


def notification_matches(item: dict, config: dict) -> list[dict] | None:
    """Return matching rows, [] for a matching unpartitioned notice, or None."""
    assessment = item.get("assessment") or {}
    if item.get("detail_warning") or not assessment.get("evidence_complete", False):
        return None
    if assessment.get("status") == "excluded":
        return None
    # If the parser finds multiple positions but cannot map all of them,
    # matching positions are still evaluated independently.
    positions = assessment.get("positions") or []
    if positions:
        selected = [position for position in positions
                    if _row_matches(position.get("assessment", {}), config)]
        return selected if selected else None
    return [] if _row_matches(assessment, config) else None


def notification_tier(item: dict, config: dict) -> str:
    return "priority" if notification_matches(item, config) is not None else "excluded"


def matched_positions(item: dict, config: dict) -> list[dict]:
    return notification_matches(item, config) or []


def no_kpss_worker_matches(item: dict, config: dict) -> bool:
    """An explicitly KPSS-exempt public worker notice for human review only.

    This never confirms that the candidate meets other job-specific requirements.
    """
    if not config["search_settings"].get("include_jobs_without_kpss", False):
        return False
    assessment = item.get("assessment") or {}
    if item.get("detail_warning") or not assessment.get("evidence_complete", False):
        return False
    if assessment.get("status") == "excluded" or assessment.get("score_ambiguous"):
        return False
    if assessment.get("exam_year_conflict") or assessment.get("positions"):
        return False
    if not assessment.get("kpss_exempt") or assessment.get("score_conditions") or assessment.get("score_types"):
        return False
    if not any(assessment.get(key) for key in (
        "minimum_high_school_open", "general_associate_requirement",
        "general_associate_code", "department_mentioned",
    )):
        return False
    return bool(re.search(r"işçi|isci|isçi", (item.get("title", "")).casefold()))
