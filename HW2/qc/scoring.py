from __future__ import annotations

from typing import Any, Dict

# HW2 QC weights (sum used for partial credit; penalty subtracts)
WEIGHTS = {
    "numeric_accuracy_rate": 34,
    "patient_count_match_rate": 10,
    "provider_count_match_rate": 11,
    "lapsed_followup_match_rate": 9,
    "required_sections_rate": 12,
    "retrieval_check_disclosure_rate": 8,
    "limitation_disclosure_rate": 8,
    "medication_theme_mention_rate": 8,
}


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def compute_validity_score(metrics: Dict[str, Any], concision_score_1_5: float | None = None) -> Dict[str, float | bool]:
    """Weighted 0–100 validity score plus strict pass flag for HW2 reports."""
    weighted = 0.0
    for k, w in WEIGHTS.items():
        weighted += float(w) * _clamp(float(metrics.get(k, 0.0)))

    concision_norm = 0.65
    if concision_score_1_5 is not None:
        concision_norm = _clamp((float(concision_score_1_5) - 1.0) / 4.0)
    weighted += 5.0 * concision_norm

    clinically_unsupported = float(metrics.get("clinically_unsupported_number_count", 0.0))
    unsupported_pid = float(metrics.get("unsupported_patient_identifier_count", 0.0))
    unsupported_prov = float(metrics.get("unsupported_provider_count", 0.0))

    # Raw extra numerals are surfaced for debugging only; penalty targets cohort-like claims.
    hallucination_penalty = min(
        35.0,
        10.0 * clinically_unsupported + 12.0 * unsupported_pid + 6.0 * unsupported_prov,
    )

    validity = max(0.0, weighted - hallucination_penalty)

    req = float(metrics.get("required_sections_rate", 0.0))
    pcm = float(metrics.get("patient_count_match_rate", metrics.get("patient_count_match", 0.0)))
    vcm = float(metrics.get("visit_count_match_rate", metrics.get("visit_count_match", 0.0)))
    lap = float(metrics.get("lapsed_followup_match_rate", metrics.get("lapsed_followup_match", 0.0)))

    passed = bool(
        validity >= 80.0
        and req == 1.0
        and pcm == 1.0
        and vcm == 1.0
        and lap == 1.0
        and unsupported_pid == 0.0
        and unsupported_prov == 0.0
        and clinically_unsupported == 0.0
    )

    return {
        "validity_score_0_100": round(float(validity), 2),
        "passed_absolute_validity": passed,
    }
