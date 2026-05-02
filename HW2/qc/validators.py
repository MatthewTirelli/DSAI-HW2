from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

# Prompt A (baseline): fewer mandatory sections — still clinically structured
HW2_BASELINE_SECTION_HEADERS = [
    "## Executive summary",
    "## Cohort overview",
    "## Limitations",
]

# Prompt B (grounded executive): validator-friendly full outline
HW2_GROUNDED_SECTION_HEADERS = [
    "## Executive summary",
    "## Cohort overview",
    "## Provider and access patterns",
    "## Medication and documentation themes",
    "## Lapsed follow-up and care continuity",
    "## Data reliability and QC notes",
    "## Limitations",
]


def _collapse_ws_lower(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _header_present_in_report(report_text: str, header: str) -> bool:
    return _collapse_ws_lower(header) in _collapse_ws_lower(report_text)


def _to_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _whole_word_int_pattern(n: int) -> re.Pattern[str]:
    return re.compile(rf"\b{int(n)}\b")


def _numbers_in_text(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"\b\d+\b", text)]


# --- Extra vs clinically-unsupported numerals (QC debugging) ---

_RE_INCIDENTAL_SNIPPET = re.compile(
    r"""
    prompt\s+[ab]\b
    |(?:^|\s)baseline\b.*\bgrounded\b|\bgrounded\b.*\bbaseline\b
    |\b(?:table|figure|appendix)\s+\d+
    |\bsection\s+\d+\b
    |\b(?:chapter|part)\s+\d+\b
    |95\s*%\s*(?:ci|c\.?i\.?)
    |\bconfidence\s+interval\b|\binterval\s+estimates?\b
    |\bp\s*[<>=]=?\s*\d*\.?\d+
    |\b0\s*[–-]\s*100\b|\(0\s*[–-]\s*100\)
    |\bscale\b[^\n]{0,30}\b100\b|\b0\s+to\s+100\b
    |\bvalidity\s+score\b[^\n]{0,40}\b100\b
    |^\s*#{1,6}[^\n]*\d
    |\[[\d,\s]+\]
    |synthetic|mock|illustrative\s+example|not\s+real\s+patients?\b
    """,
    re.I | re.VERBOSE | re.M,
)

_RE_CLINICAL_CUE = re.compile(
    r"""
    \bpatients?\b|\bpatient\s+(?:count|ids?|total|cohort)\b|\bunique\s+patients?\b|\bcohort\b
    |\bvisits?\b|\b(?:qualifying|eligible|high[-\s]?risk)\s+visits?\b|\bencounters?\b
    |\bproviders?\b|\bclinicians?\b|\bprescri(?:ber|ptions?)\b|\bbedside\b|\bcaseload\b
    |\blapsed\b|\boverdue\b|follow[-\s]?up\b
    |\bmedications?\b|\bmeds?\b|antidepress|ssris?\b|\bpsychiatr
    |\bphq\b|\bgad(?:-?7)?\b|\bdepress(?:ion|ive)\s+scores?\b|\bscreening\b
    |\bscores?\b
    |\d{1,2}/\d{1,2}/\d{2,4}
    |\b(?:19|20)\d{2}\b
    |\bpercent|\bproportion|\bprevalence|\bincidence\b|\brate\b|\d\s*%
    |\b(?:average|mean|median)\b|\brange\b
    |\brank(?:ing|s)?\b|\btop\s+\d+\b
    |\bn\s*[≈=]\s*\d+
    """,
    re.I | re.VERBOSE,
)

_RE_SMALL_ORDINAL_PHRASE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:themes?|sections?|areas?|domains?|categories?|bullets?|points?)\b",
    re.I,
)

_MISSING_ECHO_PARTIAL = 0.55

_PAT_TOTAL_PATTERNS = [
    re.compile(r"(?i)(?:distinct|unique)\s+cohort\s+patients?\s*[:#=\-\s]+\s*(\d+)"),
    re.compile(r"(?i)total\s+patients?\s*[:#=\-\s]+\s*(\d+)"),
    re.compile(r"(?i)cohort\s+patients?\s*\(distinct\)\s*[:#=\-\s]+\s*(\d+)"),
]
_VISIT_TOTAL_PATTERNS = [
    re.compile(r"(?i)total\s+visits?\s*[:#=\-\s]+\s*(\d+)"),
    re.compile(r"(?i)qualifying\s+visits?\s*[:#=\-\s]+\s*(\d+)"),
]
_LAPSED_PATTERNS = [
    re.compile(r"(?i)lapsed\s+follow(?:\s*[-:]?\s*)?up\s+(?:rows?\s+)?count\s*[:#=\-\s]+\s*(\d+)"),
    re.compile(r"(?i)lapsed\s+follow(?:\s*[-:]?\s*)?up\s+count\s*[:#=\-\s]+\s*(\d+)"),
]


def _grade_scalar_echo(
    text: str,
    expected: int,
    patterns: list[re.Pattern[str]],
    *,
    whole_word_fallback: bool,
    vacuous_true: bool = False,
    vacuous_reported: int | None = 0,
) -> tuple[float, str, int | None]:
    """Returns (weighted component 1 / partial / 0, status token, reported int or None)."""
    if vacuous_true:
        return (1.0, "vacuous_ok", vacuous_reported)

    ints: list[int] = []
    for p in patterns:
        for m in p.finditer(text):
            g = m.group(1)
            if g.isdigit():
                ints.append(int(g))

    if ints:
        if any(v != expected for v in ints):
            bad = next((v for v in ints if v != expected), ints[0])
            return (0.0, "incorrect", bad)
        return (1.0, "labeled_exact", expected)

    if whole_word_fallback and _whole_word_int_pattern(expected).search(text):
        return (1.0, "fallback_echo", expected)

    return (_MISSING_ECHO_PARTIAL, "missing_or_unlabeled", None)


def _numeric_accuracy_aggregate(
    visit_c: float,
    patient_c: float,
    lapsed_c: float,
    provider_rate: float,
    *,
    n_providers: int,
) -> float:
    if n_providers > 0:
        w_v, w_p, w_l, w_pr = 0.32, 0.32, 0.26, 0.10
        return w_v * visit_c + w_p * patient_c + w_l * lapsed_c + w_pr * float(provider_rate)
    w_v, w_p, w_l = 0.42, 0.33, 0.25
    return w_v * visit_c + w_p * patient_c + w_l * lapsed_c


def _snippet(text: str, start: int, end: int, radius: int = 110) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi]


def _is_ordered_list_marker_at_line_start(text: str, start: int, end: int, value: int) -> bool:
    """True if this number is the leading ordered-list index (1. or 1) ) on its line."""
    ls = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    line = text[ls:line_end]
    rel = start - ls
    m = re.match(r"^(\s*)(\d+)(\.(?=\s)|\))(?=\s)", line)
    if not m:
        return False
    try:
        v = int(m.group(2))
    except ValueError:
        return False
    idx = m.start(2)
    return v == value and idx == rel


def _classify_extra_integers(
    text: str,
    allowed: set[int],
) -> tuple[
    int,
    int,
    list[int],
    list[int],
    list[tuple[int, str]],
    list[tuple[int, str]],
]:
    """
    Returns:
      extra_number_count_raw (unique extras, capped 50; matches legacy \"many extras\" signal)
      clinically_unsupported_number_count (flagged occurrences, capped 50)
      sorted unique raw extras
      sorted unique clinically unsupported values
      list of (val, snippet) for raw extra occurrences (capped 40 for CSV)
      list of (val, snippet) for clinically flagged occurrences (capped 40)
    """
    raw_snips: list[tuple[int, str]] = []
    clinical_snips: list[tuple[int, str]] = []
    raw_values: set[int] = set()
    clinical_values: set[int] = set()
    clinical_occurrences = 0

    for m in re.finditer(r"\b(\d+)\b", text):
        val = int(m.group(1))
        if val in allowed or val <= 1:
            continue
        a, b = m.start(1), m.end(1)
        sn = _snippet(text, a, b)
        sl = sn.lower()
        raw_values.add(val)
        if len(raw_snips) < 40:
            raw_snips.append((val, sn.replace("\n", " ").strip()))

        if _RE_INCIDENTAL_SNIPPET.search(sl):
            continue
        if _RE_SMALL_ORDINAL_PHRASE.search(sl):
            continue
        if _is_ordered_list_marker_at_line_start(text, a, b, val):
            continue
        if not _RE_CLINICAL_CUE.search(sl):
            continue

        clinical_occurrences += 1
        clinical_values.add(val)
        if len(clinical_snips) < 40:
            clinical_snips.append((val, sn.replace("\n", " ").strip()))

    raw_count = min(50, len(raw_values))
    clinical_count = min(50, clinical_occurrences)

    return (
        raw_count,
        clinical_count,
        sorted(raw_values),
        sorted(clinical_values),
        raw_snips,
        clinical_snips,
    )


def extract_hw2_ground_truth(
    cohort_df: pd.DataFrame,
    retrieval_payload: dict[str, Any],
    verification_json: dict[str, Any],
) -> dict[str, Any]:
    """Structured facts from deterministic cohort/payload — used only for QC, not diagnostic truth."""
    n_visits = int(len(cohort_df))
    if "patient_id" in cohort_df.columns:
        cohort_patient_ids = sorted({i for x in cohort_df["patient_id"].dropna() if (i := _to_int(x)) is not None})
        n_patients = int(cohort_df["patient_id"].nunique())
    else:
        cohort_patient_ids = []
        n_patients = 0

    prov = retrieval_payload.get("provider_concentration") or {}
    provider_visit_counts: dict[str, int] = {}
    for r in prov.get("visit_counts_by_provider") or []:
        name = str(r.get("provider", "")).strip()
        vc = _to_int(r.get("visit_count"))
        if name and vc is not None:
            provider_visit_counts[name] = vc

    lf = retrieval_payload.get("lapsed_followup") or {}
    rows = lf.get("retrieval_rows") or []
    rc = _to_int(lf.get("row_count"))
    lapsed_followup_count = int(rc if rc is not None else len(rows))

    med = retrieval_payload.get("medications_summary") or retrieval_payload.get("medication_summary") or {}
    med_top = [str(x.get("text", "")) for x in (med.get("top_medication_strings") or [])][:15]

    checks = verification_json.get("checks") or []
    all_passed = bool(verification_json.get("all_passed", False))

    allowed_numeric: set[int] = {
        n_visits,
        n_patients,
        lapsed_followup_count,
        *cohort_patient_ids,
        *provider_visit_counts.values(),
    }

    canon_providers = set(provider_visit_counts.keys())
    if "provider" in cohort_df.columns:
        canon_providers |= {str(x).strip() for x in cohort_df["provider"].dropna().unique() if str(x).strip()}

    return {
        "n_visits": n_visits,
        "n_patients": n_patients,
        "cohort_patient_ids": cohort_patient_ids,
        "provider_visit_counts": provider_visit_counts,
        "lapsed_followup_count": lapsed_followup_count,
        "medication_theme_strings": med_top,
        "retrieval_checks": checks,
        "all_retrieval_checks_passed": all_passed,
        "allowed_numeric_set": sorted(allowed_numeric),
        "canonical_provider_names": sorted(canon_providers),
    }


def validate_hw2_report(
    report_text: str,
    ground_truth: dict[str, Any],
    *,
    section_headers: list[str] | None = None,
) -> dict[str, Any]:
    """Compute alignment metrics between Markdown report text and deterministic ground truth."""
    text = report_text or ""
    lower = text.lower()

    headers = section_headers if section_headers is not None else HW2_GROUNDED_SECTION_HEADERS
    required_present = sum(1 for h in headers if _header_present_in_report(text, h))
    required_sections_rate = required_present / max(1, len(headers))

    n_visits = int(ground_truth.get("n_visits") or 0)
    n_patients = int(ground_truth.get("n_patients") or 0)
    lapsed = int(ground_truth.get("lapsed_followup_count") or 0)
    prov_counts: dict[str, int] = dict(ground_truth.get("provider_visit_counts") or {})
    cohort_ids: list[int] = list(ground_truth.get("cohort_patient_ids") or [])
    allowed = set(int(x) for x in (ground_truth.get("allowed_numeric_set") or []))
    canon_prov = set(str(x) for x in (ground_truth.get("canonical_provider_names") or []))

    visit_comp, visit_st, rep_visit = _grade_scalar_echo(
        text,
        n_visits,
        _VISIT_TOTAL_PATTERNS,
        whole_word_fallback=True,
        vacuous_true=n_visits <= 0,
        vacuous_reported=0,
    )
    patient_comp, patient_st, rep_patient = _grade_scalar_echo(
        text,
        n_patients,
        _PAT_TOTAL_PATTERNS,
        whole_word_fallback=True,
        vacuous_true=n_patients <= 0,
        vacuous_reported=0,
    )
    lapsed_comp, lapsed_st, rep_lapsed = _grade_scalar_echo(
        text,
        lapsed,
        _LAPSED_PATTERNS,
        whole_word_fallback=True,
        vacuous_true=lapsed <= 0,
        vacuous_reported=0,
    )

    prov_hits = 0
    for pname, cnt in prov_counts.items():
        if len(pname) < 2:
            continue
        if pname.lower() in lower and _whole_word_int_pattern(int(cnt)).search(text):
            prov_hits += 1
    n_prov = len([p for p in prov_counts if len(str(p).strip()) >= 2])
    provider_count_match_rate = prov_hits / max(1, n_prov) if n_prov else 1.0

    visit_count_match = float(visit_comp)
    patient_count_match = float(patient_comp)
    lapsed_followup_match = float(lapsed_comp)
    numeric_accuracy_score = float(
        _numeric_accuracy_aggregate(
            visit_comp,
            patient_comp,
            lapsed_comp,
            provider_count_match_rate,
            n_providers=n_prov,
        )
    )

    mismatch_flags: list[str] = []
    if visit_st == "incorrect":
        mismatch_flags.append("visit_count_incorrect")
    elif visit_st == "missing_or_unlabeled" and n_visits > 0:
        mismatch_flags.append("visit_count_missing_weak_echo")
    if patient_st == "incorrect":
        mismatch_flags.append("patient_count_incorrect")
    elif patient_st == "missing_or_unlabeled" and n_patients > 0:
        mismatch_flags.append("patient_count_missing_weak_echo")
    if lapsed_st == "incorrect":
        mismatch_flags.append("lapsed_followup_incorrect")
    elif lapsed_st == "missing_or_unlabeled" and lapsed > 0:
        mismatch_flags.append("lapsed_followup_missing_weak_echo")

    patient_count_match_rate = patient_count_match
    lapsed_followup_match_rate = lapsed_followup_match
    numeric_accuracy_rate = numeric_accuracy_score
    visit_count_match_rate = visit_count_match

    def _rep_col(v: int | None) -> int:
        return int(v) if v is not None else -1

    all_ok = bool(ground_truth.get("all_retrieval_checks_passed"))
    disclosed = bool(
        re.search(
            r"retrieval\s+verification|verification\s+checks|all\s+checks\s+passed|qc\s+notes|data\s+reliability",
            lower,
        )
    )
    retrieval_check_disclosure_rate = 1.0 if (all_ok and disclosed) or (not all_ok and disclosed) else (0.5 if disclosed else 0.0)

    (
        extra_number_count_raw,
        clinically_unsupported_number_count,
        raw_unique_sorted,
        clinical_unique_sorted,
        _raw_snips,
        clinical_snips,
    ) = _classify_extra_integers(text, allowed)

    hallucinated_number_count = float(extra_number_count_raw)

    allowed_numbers = json.dumps(sorted(allowed), ensure_ascii=False)
    raw_extra_numbers = json.dumps(raw_unique_sorted, ensure_ascii=False)
    clinically_unsupported_numbers = json.dumps(clinical_unique_sorted, ensure_ascii=False)
    clinically_unsupported_number_context = json.dumps(
        [{"value": v, "context": s[:400]} for v, s in clinical_snips],
        ensure_ascii=False,
    )

    pid_labels = re.findall(
        r"(?:patient\s*id|patient\s*#|id\s*[:#])\s*(\d+)",
        text,
        flags=re.I,
    )
    mentioned_pids = {int(x) for x in pid_labels if x.isdigit()}
    cohort_set = set(cohort_ids)
    unsupported_patient_identifier_count = len(mentioned_pids - cohort_set) if cohort_set else 0

    unsupported_provider_count = 0
    for line in text.splitlines():
        low = line.lower()
        if "provider" not in low and "dr." not in low:
            continue
        for chunk in re.findall(r"\bDr\.?\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b", line):
            if chunk not in canon_prov and not any(chunk in c or c in chunk for c in canon_prov if len(c) > 3):
                unsupported_provider_count += 1
    unsupported_provider_count = min(unsupported_provider_count, 25)

    lim_ok = "## limitations" in lower or "limitations" in lower
    audit_ok = "audit" in lower or "qc" in lower or "synthetic" in lower or "educational" in lower
    limitation_disclosure_rate = (1.0 if lim_ok else 0.0) * 0.7 + (0.3 if audit_ok else 0.0)

    med_strings = [m.lower() for m in (ground_truth.get("medication_theme_strings") or []) if m]
    med_hits = sum(1 for m in med_strings[:8] if m and m[:40] in lower)
    medication_theme_mention_rate = med_hits / max(1, min(8, len(med_strings) or 1))

    return {
        "required_sections_rate": required_sections_rate,
        "numeric_accuracy_rate": numeric_accuracy_rate,
        "numeric_accuracy_score": numeric_accuracy_score,
        "patient_count_match": patient_count_match,
        "visit_count_match": visit_count_match,
        "lapsed_followup_match": lapsed_followup_match,
        "patient_count_match_rate": patient_count_match_rate,
        "visit_count_match_rate": visit_count_match_rate,
        "provider_count_match_rate": provider_count_match_rate,
        "lapsed_followup_match_rate": lapsed_followup_match_rate,
        "expected_patient_count": int(n_patients),
        "reported_patient_count": _rep_col(rep_patient),
        "expected_visit_count": int(n_visits),
        "reported_visit_count": _rep_col(rep_visit),
        "expected_lapsed_followup": int(lapsed),
        "reported_lapsed_followup": _rep_col(rep_lapsed),
        "numeric_mismatch_flags": json.dumps(mismatch_flags, ensure_ascii=False),
        "retrieval_check_disclosure_rate": retrieval_check_disclosure_rate,
        "extra_number_count_raw": float(extra_number_count_raw),
        "clinically_unsupported_number_count": float(clinically_unsupported_number_count),
        "hallucinated_number_count": float(hallucinated_number_count),
        "allowed_numbers": allowed_numbers,
        "raw_extra_numbers": raw_extra_numbers,
        "clinically_unsupported_numbers": clinically_unsupported_numbers,
        "clinically_unsupported_number_context": clinically_unsupported_number_context,
        "unsupported_patient_identifier_count": float(unsupported_patient_identifier_count),
        "unsupported_provider_count": float(unsupported_provider_count),
        "limitation_disclosure_rate": limitation_disclosure_rate,
        "medication_theme_mention_rate": medication_theme_mention_rate,
        # Aliases for statistical_analysis / legacy column names
        "hallucinated_state_count": 0.0,
        "confidence_label_match_rate": 1.0,
        "low_confidence_disclosure_rate": 1.0,
        "confidence_score_match_rate": 1.0,
        "unsupported_confidence_claim_count": 0.0,
        "confidence_misuse": [],
    }


def section_headers_for_mode(mode: str) -> list[str]:
    m = (mode or "").lower().strip()
    if m == "grounded":
        return list(HW2_GROUNDED_SECTION_HEADERS)
    return list(HW2_BASELINE_SECTION_HEADERS)
