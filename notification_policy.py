"""Conservative Telegram routing: likely matches now, uncertain notices in a digest.

A priority match only checks verified P93 threshold and education evidence.
It does NOT certify all application requirements.
"""
from __future__ import annotations


def _score_and_education_match(assessment: dict, candidate: dict) -> bool:
    if assessment.get("status") == "excluded":
        return False
    if assessment.get("score_ambiguous") or not assessment.get("evidence_complete", True):
        return False
    if assessment.get("special_conditions_detected") or assessment.get("exam_year_conflict"):
        return False
    conditions = assessment.get("score_conditions", [])
    if len(conditions) != 1:
        return False
    condition = conditions[0]
    if condition["score_type"] != candidate.get("kpss_score_type"):
        return False
    # Never silently round a reported approximate 70.6 up to 71.
    if float(condition["minimum_score"]) > float(candidate["kpss_score"]):
        return False
    return bool(assessment.get("general_associate_code") or assessment.get("department_mentioned"))


def notification_tier(item: dict, config: dict) -> str:
    """Return priority, review, or excluded; uncertainty always stays review."""
    assessment = item.get("assessment") or {}
    if assessment.get("status") == "excluded" and not item.get("detail_warning"):
        return "excluded"
    if item.get("detail_warning") or not assessment.get("evidence_complete", True):
        return "review"
    if assessment.get("special_conditions_detected") or assessment.get("exam_year_conflict"):
        return "review"
    positions = assessment.get("positions", [])
    candidate = config["candidate"]
    if positions:
        if any(_score_and_education_match(p.get("assessment", {}), candidate) for p in positions):
            return "priority"
        # A partially parsed mixed posting must not be lost.
        return "review"
    return "priority" if _score_and_education_match(assessment, candidate) else "review"


def matched_positions(item: dict, config: dict) -> list[dict]:
    """Only the visibly P93/education-matching rows belong in a priority alert."""
    positions = (item.get("assessment") or {}).get("positions", [])
    return [p for p in positions
            if _score_and_education_match(p.get("assessment", {}), config["candidate"])]


def review_digest_message(items: list[dict], config: dict) -> str:
    candidate = config["candidate"]
    score = str(candidate["kpss_score"]).replace(".", ",")
    lines = [
        "📋 KPSS P93 — GÜNLÜK İNCELEME LİSTESİ",
        f"Profil: Ön lisans / yaklaşık {score} puan",
        "Aşağıdaki ilanların şartları tam doğrulanamadı; kesin uygunluk anlamına gelmez.",
    ]
    for n, item in enumerate(items, 1):
        title = " ".join(item.get("title", "İlan").split())[:95]
        lines.extend(["", f"{n}. {title}", item["url"][:430]])
    lines.extend(["", "Önce resmî ilanı ve bütün özel şartları kontrol edin."])
    # Telegram's sendMessage text limit is 4096 characters.
    return "\n".join(lines)[:3900]
