from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pandas as pd


def _fmt_pass_rate_wilson(row: Dict[str, Any]) -> str:
    pr = 100.0 * float(row.get("pass_rate", 0.0))
    lo = 100.0 * float(row.get("pass_rate_wilson_low", 0.0))
    hi = 100.0 * float(row.get("pass_rate_wilson_high", 0.0))
    return f"{pr:.1f}% [95% CI: {lo:.1f}–{hi:.1f}]"


def write_summary_markdown(summary: Dict[str, Any], df: pd.DataFrame, out_path: Path) -> None:
    absv = summary.get("absolute_validity", {})
    comp = summary.get("comparative", {})
    conf = summary.get("confidence_validation", {})
    fma = summary.get("failure_mode_analysis") or {}
    by_mode = absv.get("by_mode") or {}
    prc = summary.get("pass_rate_comparison")

    lines = [
        "# QC Validation Summary",
        "",
        "## 1. Absolute validity",
        "",
        "**Overall (all outputs):**",
        f"- Report quality score (mean): {absv.get('mean_score', 0):.2f}" if absv else "- Report quality score (mean): n/a",
        f"- 95% CI: [{absv.get('ci95_low', 0):.2f}, {absv.get('ci95_high', 0):.2f}]" if absv else "- 95% CI: n/a",
        f"- Pass rate: {100*absv.get('pass_rate', 0):.1f}%",
        "",
        "**By prompt mode:**",
        "",
    ]
    if by_mode.get("baseline"):
        b = by_mode["baseline"]
        lines.extend(
            [
                "**Prompt A / Baseline:**",
                f"- Report quality score (mean): {b['mean_validity']:.2f}",
                f"- Pass rate: {_fmt_pass_rate_wilson(b)}",
                f"- Structural error rate: {100*b['hallucination_rate']:.1f}%",
                "",
            ]
        )
    else:
        lines.extend(["**Prompt A / Baseline:**", "- _No Prompt A baseline rows in this run._", ""])

    if by_mode.get("grounded"):
        g = by_mode["grounded"]
        lines.extend(
            [
                "**Prompt B / Grounded Executive Report:**",
                f"- Report quality score (mean): {g['mean_validity']:.2f}",
                f"- Pass rate: {_fmt_pass_rate_wilson(g)}",
                f"- Structural error rate: {100*g['hallucination_rate']:.1f}%",
                "",
            ]
        )
    else:
        lines.extend(["**Prompt B / Grounded Executive Report:**", "- _No Prompt B grounded rows in this run._", ""])

    lines.extend(
        [
            "Prompt B / Grounded Executive Report is shown as the primary report in the app; compare against Prompt A / Baseline for QC.",
            "",
        ]
    )

    if by_mode.get("baseline") and by_mode.get("grounded"):
        b, g = by_mode["baseline"], by_mode["grounded"]
        if g["mean_validity"] > b["mean_validity"] and g["pass_rate"] > b["pass_rate"]:
            lines.extend(
                [
                    "Prompt B / Grounded Executive Report achieved higher validity and pass rates than Prompt A / Baseline in this run.",
                    "",
                ]
            )

    lines.extend(
        [
            (
                "- Strict pass criteria emphasize sections, cohort numeric fidelity (visits/patients/lapsed follow-up), "
                "provider count alignment, and hallucination proxies (extra identifiers/numbers)."
            ),
            ("- Signals are heuristic on synthetic/educational data — not diagnostic or regulatory validation."),
            "",
            "## Pass-rate comparison",
            "",
        ]
    )

    if prc:
        bpr = 100.0 * float(prc.get("baseline_pass_rate", 0.0))
        gpr = 100.0 * float(prc.get("grounded_pass_rate", 0.0))
        dpr = 100.0 * float(prc.get("pass_rate_difference", 0.0))
        dlo = 100.0 * float(prc.get("difference_ci95_low", 0.0))
        dhi = 100.0 * float(prc.get("difference_ci95_high", 0.0))
        w_b = by_mode.get("baseline") or {}
        w_g = by_mode.get("grounded") or {}
        b_w = _fmt_pass_rate_wilson(w_b) if w_b else f"{bpr:.1f}%"
        g_w = _fmt_pass_rate_wilson(w_g) if w_g else f"{gpr:.1f}%"
        lines.append(f"- Prompt A / Baseline pass rate: {b_w}")
        lines.append(f"- Prompt B / Grounded Executive Report pass rate: {g_w}")
        lines.append(f"- Difference (Prompt B − Prompt A): {dpr:.1f} pp [95% bootstrap CI: {dlo:.1f}–{dhi:.1f}]")
        note = prc.get("mcnemar_note")
        pv = prc.get("mcnemar_p_value")
        method = prc.get("mcnemar_method", "")
        if note:
            lines.extend(["", note, ""])
        elif pv is not None:
            sig = float(pv) < 0.05
            lines.append(
                f"- McNemar ({method}): p = {float(pv):.4g} "
                f"(discordant pairs: baseline pass / grounded fail = {prc.get('mcnemar_b')}, "
                f"baseline fail / grounded pass = {prc.get('mcnemar_c')})."
            )
            lines.append("")
            lines.append(
                "Pass rate improvement for Prompt B vs Prompt A is statistically significant under McNemar."
                if sig
                else "Pass rate difference was not statistically significant under the paired McNemar test."
            )
            lines.append("")
        else:
            lines.append("")
    else:
        lines.extend(
            [
                "_Paired pass-rate comparison unavailable (need both Prompt A and Prompt B rows per `trial_id`)._",
                "",
            ]
        )

    lines.extend(["", "## 2. Prompt A / Baseline vs Prompt B / Grounded Executive Report"])
    if comp:
        pts = comp.get("paired_t_stat")
        d = comp.get("effect_size_cohens_d")
        try:
            pts_f = f"{float(pts):.2f}" if pts is not None else str(pts)
        except (TypeError, ValueError):
            pts_f = str(pts)
        try:
            d_f = f"{float(d):.2f}" if d is not None else str(d)
        except (TypeError, ValueError):
            d_f = str(d)
        lines.extend(
            [
                f"- Prompt A / Baseline mean validity score: {comp.get('baseline_mean', 0):.2f}",
                f"- Prompt B / Grounded Executive Report mean validity score: {comp.get('grounded_mean', 0):.2f}",
                f"- Paired t-statistic: {pts_f}",
                f"- Effect size (Cohen's d): {d_f}",
            ]
        )
    else:
        lines.append("- Comparative results unavailable (run compare mode).")

    lines.extend(["", "## 3. Confidence validation"])
    diverse = bool(conf.get("diverse_labels_for_calibration"))
    by_label = conf.get("by_label_summary") or []
    if not diverse:
        lines.append(
            "Confidence-validation-by-label plots are retained for QC framework compatibility but are not primary for HW2 (dominant confidence is often neutral)."
        )
    if by_label:
        lines.append("")
        lines.append("Summary by dominant confidence label (structured payload):")
        for g in by_label:
            lines.append(
                f"- {g['confidence_label'].title()}: report quality score {g['mean_validity_score']:.2f}, "
                f"numeric alignment {100*float(g['numeric_accuracy']):.1f}%, pass rate {100*g['pass_rate']:.1f}%"
            )
    groups = conf.get("groups") or []
    if diverse and groups:
        hi = next((g for g in groups if g["confidence_label"] == "high"), None)
        lo = next((g for g in groups if g["confidence_label"] == "low"), None)
        if hi and lo:
            lines.append(
                f"- High-confidence trials: report quality score {hi['mean_validity_score']:.2f}; "
                f"low-confidence trials: {lo['mean_validity_score']:.2f}. Calibration flag: {conf.get('calibration_flag', 'unknown')}."
            )
    elif not by_label:
        lines.append("- Dominant confidence label not available in results.")

    corr = conf.get("confidence_score_validity_correlation")
    lines.append("")
    if corr is not None:
        lines.append(f"Correlation between upstream confidence score and report quality score: r = {corr:.2f}.")
    else:
        lines.append(
            "Confidence score correlation was not computed (missing or insufficient variation in confidence scores)."
        )

    lines.extend(["", "## 4. Failure Mode Analysis", ""])
    fm_rows = fma.get("table") or []
    if fm_rows:
        lines.append("Common QC signals reflect section coverage, cohort numeric fidelity, and hallucination heuristics.")
        lines.append("")
        lines.extend(["| Failure mode | Count | Share |", "|---|---:|---:|"])
        for r in fm_rows:
            lines.append(f"| {r['name']} | {r['count']} | {r['percent']:.1f}% |")
        lines.append("")
        lines.append("Review row-level CSV columns for detailed validity metrics.")
        lines.append("")
        lines.append(fma.get("interpretation", ""))
    else:
        lines.append("_No failure-mode aggregation (empty dataset)._")

    lines.extend(
        [
            "",
            "## 5. Row-level review",
            "- Inspect `qc_results.csv`: Potential Unsupported Statements, Confidence Wording Issues, grader payload, and report text (column names remain machine-readable).",
            "",
            "## 6. Example outputs",
        ]
    )
    if not df.empty:
        sample_cols = [
            "mode",
            "trial_id",
            "validity_score_0_100",
            "numeric_accuracy_score",
            "visit_count_match",
            "patient_count_match",
            "clinically_unsupported_number_count",
            "unsupported_patient_identifier_count",
        ]
        sample_cols = [c for c in sample_cols if c in df.columns]
        sample = df.head(3)[sample_cols] if sample_cols else df.head(3)
        lines.append("")
        lines.append("```text")
        lines.append(sample.to_string(index=False))
        lines.append("```")

    out_path.write_text("\n".join(lines))
