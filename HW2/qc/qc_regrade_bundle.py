"""Shared no-OpenAI QC regrading: validators + scoring + stats + summaries."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

STATIC_REPORT_KEYS = frozenset(
    {
        "trial_id",
        "mode",
        "runtime_s",
        "dominant_confidence_label",
        "dominant_confidence_score",
        "report_text",
        "unsupported_claims",
        "missing_required_elements",
        "grader_payload",
    }
)


def regraded_dataframe(
    *,
    qc_csv: Path,
    cohort_df,
    retrieval_payload: dict,
    verification_json: dict,
) -> pd.DataFrame:
    from qc.scoring import compute_validity_score
    from qc.validators import extract_hw2_ground_truth, section_headers_for_mode, validate_hw2_report

    gt = extract_hw2_ground_truth(cohort_df, retrieval_payload, verification_json)
    df_in = pd.read_csv(qc_csv)
    rows: list[dict] = []

    for _, r in df_in.iterrows():
        report_text = str(r.get("report_text", ""))
        mode = str(r.get("mode", "grounded"))
        base = {k: r[k] for k in STATIC_REPORT_KEYS if k in r.index}
        metrics = validate_hw2_report(report_text, gt, section_headers=section_headers_for_mode(mode))
        score = compute_validity_score(metrics, concision_score_1_5=None)
        row = {**base, **metrics, **score}
        row["confidence_misuse"] = json.dumps(metrics.get("confidence_misuse", []), ensure_ascii=False)
        rows.append(row)

    return pd.DataFrame(rows)


def write_regraded_artifacts(df: pd.DataFrame, *, out_dir: Path, csv_name: str, md_name: str) -> None:
    from qc.report_generation import write_summary_markdown
    from qc.statistical_analysis import analyze_results

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / csv_name).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / csv_name, index=False)
    summary = analyze_results(df)
    write_summary_markdown(summary, df, out_dir / md_name)
