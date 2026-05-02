"""
Live Prompt B grading card — HTML/CSS ported from ToolV2 Streamlit qc_panel.py
(`_LIVE_VALIDATION_CSS` + score/check layout). Criteria rows match HW2 `qc/scoring.py` strict pass.
"""

from __future__ import annotations

import html
from typing import Any

from qc.validators import HW2_GROUNDED_SECTION_HEADERS, _header_present_in_report

# Styling copied verbatim from ToolV2 `app/qc_panel.py`, plus `.lv-section-title` used beside the expander there.
LIVE_VALIDATION_CSS = """
<style>
.lv-scope { font-size: 0.95rem; line-height: 1.55; color: #334155;
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.lv-scope .lv-title { font-size: 1.08rem; font-weight: 600; color: #0f172a; margin: 0 0 0.5rem 0; letter-spacing: -0.01em; }
.lv-scope .lv-muted { color: #64748b; font-size: 0.875rem; line-height: 1.5; margin: 0 0 1rem 0; }
.lv-scope .lv-flex { display: flex; flex-wrap: wrap; gap: 1rem; align-items: stretch; margin-bottom: 1rem; }
.lv-scope .lv-card { flex: 1 1 240px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 1rem 1.1rem; }
.lv-scope .lv-card-highlight { border-color: #cbd5e1; background: #fff; box-shadow: 0 1px 2px rgba(15,23,42,0.06); }
.lv-scope .lv-label { font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: #64748b; margin-bottom: 0.25rem; }
.lv-scope .lv-score { font-size: 2rem; font-weight: 700; color: #0f172a; line-height: 1.2; }
.lv-scope .lv-badge { display: inline-block; margin-top: 0.65rem; padding: 0.35rem 0.65rem; border-radius: 999px; font-size: 0.8rem; font-weight: 600; }
.lv-scope .lv-badge-yes { background: #dcfce7; color: #166534; border: 1px solid #bbf7d0; }
.lv-scope .lv-badge-no { background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }
.lv-scope .lv-subchecks { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
.lv-scope .lv-subchecks th { text-align: left; color: #64748b; font-weight: 600; font-size: 0.72rem; text-transform: uppercase;
  letter-spacing: 0.04em; padding: 0.4rem 0.5rem; border-bottom: 1px solid #e2e8f0; background: #f8fafc; }
.lv-scope .lv-subchecks td { padding: 0.5rem 0.5rem; border-bottom: 1px solid #f1f5f9; vertical-align: top; }
.lv-scope .lv-ok { color: #166534; font-weight: 600; white-space: nowrap; }
.lv-scope .lv-bad { color: #b91c1c; font-weight: 600; white-space: nowrap; }
.lv-scope code { font-size: 0.84em; background: #e2e8f0; padding: 0.08em 0.28em; border-radius: 4px; color: #0f172a; }
.lv-section-title { font-size: 1.05rem; font-weight: 600; color: #0f172a; margin: 1.25rem 0 0.5rem 0; }
</style>
"""


def grounded_section_headers_missing(report_text: str) -> list[str]:
    return [h for h in HW2_GROUNDED_SECTION_HEADERS if not _header_present_in_report(report_text, h)]


def _fmt_rep(v: Any) -> str:
    if v is None or v == "":
        return "—"
    try:
        i = int(v)
        return "—" if i < 0 else str(i)
    except (TypeError, ValueError):
        return str(v)


def _hw2_live_checklist_rows(
    metrics: dict[str, Any],
    score: dict[str, Any],
    missing_headers: list[str],
) -> list[tuple[str, bool, str]]:
    """Rows mirror `qc.scoring.compute_validity_score` strict pass (HW2)."""
    m = metrics
    v = float(score.get("validity_score_0_100") or 0)
    sec_rate = float(m.get("required_sections_rate", 0) or 0)
    cu = float(m.get("clinically_unsupported_number_count", m.get("hallucinated_number_count", 0)) or 0)
    raw_extras = int(float(m.get("extra_number_count_raw", m.get("hallucinated_number_count", 0)) or 0))
    strict_sec = sec_rate == 1.0
    strict_vcm = float(m.get("visit_count_match_rate", m.get("visit_count_match", 0)) or 0) == 1.0
    strict_pcm = float(m.get("patient_count_match_rate", m.get("patient_count_match", 0)) or 0) == 1.0
    strict_lap = float(m.get("lapsed_followup_match_rate", m.get("lapsed_followup_match", 0)) or 0) == 1.0
    strict_pid = float(m.get("unsupported_patient_identifier_count", 0) or 0) == 0.0
    strict_prov = float(m.get("unsupported_provider_count", 0) or 0) == 0.0
    strict_clin = cu == 0.0
    strict_score = v >= 80.0
    miss = ", ".join(html.escape(x) for x in missing_headers) if missing_headers else "none"

    return [
        (
            "All required headings present",
            strict_sec,
            f"Coverage {sec_rate:.0%} (needs 100%). Missing: {miss}.",
        ),
        (
            "Visit total matches source data",
            strict_vcm,
            f"Strength {float(m.get('visit_count_match_rate', 0) or 0):.2f} (needs full match). "
            f"Expected {_fmt_rep(m.get('expected_visit_count'))} / found {_fmt_rep(m.get('reported_visit_count'))}.",
        ),
        (
            "Patient total matches source data",
            strict_pcm,
            f"Strength {float(m.get('patient_count_match_rate', 0) or 0):.2f}. "
            f"Expected {_fmt_rep(m.get('expected_patient_count'))} / found {_fmt_rep(m.get('reported_patient_count'))}.",
        ),
        (
            "Lapsed follow-up count matches source data",
            strict_lap,
            f"Strength {float(m.get('lapsed_followup_match_rate', 0) or 0):.2f}. "
            f"Expected {_fmt_rep(m.get('expected_lapsed_followup'))} / found {_fmt_rep(m.get('reported_lapsed_followup'))}.",
        ),
        (
            "No unsupported patient identifiers",
            strict_pid,
            f"Issues flagged: {int(float(m.get('unsupported_patient_identifier_count', 0) or 0))} (should be 0).",
        ),
        (
            "No unsupported provider names",
            strict_prov,
            f"Issues flagged: {int(float(m.get('unsupported_provider_count', 0) or 0))} (should be 0).",
        ),
        (
            "No extra numbers implying unsupported clinical claims",
            strict_clin,
            f"Concern count {int(cu)} (numbers only in text: {raw_extras}).",
        ),
        (
            "Overall weighted score",
            strict_score,
            f"{v:.2f} out of 100 (needs 80+ with all checks above).",
        ),
    ]


def _context_blurb_hw2(*, n_visits: int, n_patients: int, retrieval_all_passed: bool) -> str:
    status = "all supporting data checks passed" if retrieval_all_passed else "supporting data checks need review"
    return (
        "<p class=\"lv-muted\" style=\"margin:0 0 1rem 0;\">"
        "Compares this <strong>grounded clinical summary</strong> against the visit list and packaged "
        f"supporting data for this run (<strong>{status}</strong>). Snapshot: "
        f"<strong>{html.escape(str(n_visits))}</strong> visits, "
        f"<strong>{html.escape(str(n_patients))}</strong> patients.</p>"
    )


def build_live_validation_html(
    *,
    report_text: str,
    metrics: dict[str, Any],
    score: dict[str, Any],
    n_visits: int,
    n_patients: int,
    retrieval_all_passed: bool,
    missing_headers: list[str],
) -> str:
    if not (report_text or "").strip():
        return (
            LIVE_VALIDATION_CSS
            + "<div class=\"lv-scope\"><p class=\"lv-muted\">"
            "Run analysis to see automated checks for the grounded summary.</p></div>"
        )

    validity = float(score.get("validity_score_0_100") or 0.0)
    passed = bool(score.get("passed_absolute_validity"))
    badge_cls = "lv-badge-yes" if passed else "lv-badge-no"
    badge_txt = "Passed all checks" if passed else "Needs review"

    rows = _hw2_live_checklist_rows(metrics, score, missing_headers)
    tbody: list[str] = []
    for label, ok, det in rows:
        st_cls = "lv-ok" if ok else "lv-bad"
        word = "Pass" if ok else "Fail"
        tbody.append(
            "<tr>"
            f"<td>{html.escape(label)}</td>"
            f'<td class="{st_cls}">{html.escape(word)}</td>'
            f"<td>{det}</td>"  # det may contain escaped miss list
            "</tr>"
        )
    tbl = (
        '<table class="lv-subchecks"><thead><tr>'
        "<th>Check</th><th>Result</th><th>Notes</th>"
        "</tr></thead><tbody>"
        + "".join(tbody)
        + "</tbody></table>"
    )

    inner = (
        _context_blurb_hw2(
            n_visits=n_visits,
            n_patients=n_patients,
            retrieval_all_passed=retrieval_all_passed,
        )
        + '<div class="lv-flex">'
        + '<div class="lv-card lv-card-highlight">'
        + '<div class="lv-label">Report quality score (0–100)</div>'
        + f'<div class="lv-score">{html.escape(f"{validity:.2f}")}</div>'
        + f'<span class="lv-badge {badge_cls}">{html.escape(badge_txt)}</span>'
        + (
            '<p class="lv-muted" style="margin-top:0.75rem;margin-bottom:0;">'
            "Weighted score after adjusting for flagged issues above. "
            "The badge shows “passed” only when each checklist row succeeds.</p>"
        )
        + "</div>"
        + f'<div class="lv-card">{tbl}</div>'
        + "</div>"
    )

    return (
        LIVE_VALIDATION_CSS
        + '<div class="lv-section-title">Summary quality checks</div>'
        + '<div class="lv-scope">'
        + inner
        + "</div>"
    )
