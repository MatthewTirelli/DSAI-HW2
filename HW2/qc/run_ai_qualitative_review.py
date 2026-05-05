#!/usr/bin/env python3
"""
Offline AI qualitative content analysis for already-generated QC reports.

Reads a completed paired QC CSV (e.g. 50 trials × 2 modes = 100 rows), sends each
report_text to an OpenAI reviewer for narrative-only scores, writes row-level results
and a statistical summary. Does not modify the Shiny app, validators, or regenerate reports.

Example:
  python qc/run_ai_qualitative_review.py \\
    --input out/qc_50trials_20260505T130841Z.csv \\
    --output out/ai_qualitative_review_results.csv \\
    --summary out/ai_qualitative_review_summary.md

If your batch CSV lives under `out/qc_batches/` (immutable archive copy), reference that path directly
with `--input` or symlink/copy it to match the `--input` path you choose.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv_loader import load_hw2_dotenv  # noqa: E402
from openai import OpenAI  # noqa: E402

load_hw2_dotenv()

REVIEWER_SYSTEM = """You are a senior clinical quality reviewer performing qualitative content analysis of an AI-generated executive report based on a synthetic high-risk behavioral health cohort.

Important:
- Evaluate narrative quality only.
- Do NOT verify numeric accuracy. A separate deterministic validator handles numeric fidelity.
- Do NOT use outside clinical knowledge to infer whether claims are correct.
- Do NOT make medical recommendations.
- Base your evaluation only on the report text provided.
- The data are synthetic and educational.

Score each dimension from 1 to 5:

Clarity:
1 = confusing, disorganized, hard to follow
3 = generally understandable but some awkward phrasing or structure
5 = clear, concise, and well-structured throughout

Clinical usefulness:
1 = not actionable or lacks meaningful insight
3 = somewhat useful but limited practical value
5 = highly useful for a care team, with key risks and patterns clearly highlighted

Coherence:
1 = disjointed, contradictory, or poorly organized
3 = mostly coherent with minor issues
5 = strong logical flow and consistent ideas

Completeness:
Expected areas include:
- overall cohort patterns
- provider/access trends
- medication/documentation themes
- follow-up or care-continuity concerns

1 = major gaps
3 = covers most expected areas but lacks depth
5 = comprehensive and well-balanced

Overall quality:
1 = poor
3 = adequate
5 = excellent

Return ONLY valid JSON:
{
  "clarity": 1,
  "clinical_usefulness": 1,
  "coherence": 1,
  "completeness": 1,
  "overall_quality": 1,
  "strengths": ["string"],
  "weaknesses": ["string"],
  "justification": "string"
}
"""

REQ_COLS = ("trial_id", "mode", "report_text")


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _parse_review_json(content: str) -> dict[str, Any]:
    raw = _strip_json_fence(content)
    return json.loads(raw)


def _ensure_int_dimension(name: str, v: Any) -> int:
    if isinstance(v, bool):
        raise ValueError(f"Invalid {name}: boolean not allowed ({v!r})")
    if isinstance(v, float) and math.isnan(v):
        raise ValueError(f"Invalid {name}: NaN not allowed")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int):
        iv = int(v)
    else:
        raise ValueError(f"Invalid {name}: expected numeric integer 1–5, got {v!r}")
    if iv < 1 or iv > 5:
        raise ValueError(f"Invalid {name}: expected integer 1–5, got {iv}")
    return iv


def _validate_scores(payload: dict[str, Any]) -> None:
    for k in (
        "clarity",
        "clinical_usefulness",
        "coherence",
        "completeness",
        "overall_quality",
    ):
        payload[k] = _ensure_int_dimension(k, payload[k])
    if not isinstance(payload.get("strengths"), list):
        raise ValueError("strengths must be a list")
    if not isinstance(payload.get("weaknesses"), list):
        raise ValueError("weaknesses must be a list")
    if not isinstance(payload.get("justification"), str):
        raise ValueError("justification must be a string")


def _default_model() -> str:
    import os

    return (
        (os.environ.get("HW2_AI_REVIEWER_MODEL") or "").strip()
        or (os.environ.get("OPENAI_MODEL") or "").strip()
        or "gpt-4o-mini"
    )


def _require_api_key() -> None:
    import os

    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        print(
            "ERROR: OPENAI_API_KEY is not set. Configure it in the repo-root `.env` or `HW2/.env`, "
            "or export it in your shell before running this script.",
            file=sys.stderr,
        )
        sys.exit(1)


def _row_key(row: pd.Series) -> tuple[Any, str]:
    return (row["trial_id"], str(row["mode"]).strip().lower())


def _paired_t_on_diffs(diffs: list[float]) -> tuple[float | None, float | None]:
    """One-sample t-test of paired differences vs 0; return (t_statistic, p_value)."""
    if len(diffs) < 2:
        return None, None
    arr = np.asarray(diffs, dtype=float)
    try:
        from scipy import stats as scipy_stats

        try:
            r = scipy_stats.ttest_1samp(arr, 0.0, alternative="two-sided")
        except TypeError:
            r = scipy_stats.ttest_1samp(arr, 0.0)
        stat = float(r.statistic)
        pv = float(r.pvalue)
        return stat if math.isfinite(stat) else None, pv if math.isfinite(pv) else None
    except ImportError:
        md = mean(diffs)
        sd = pstdev(diffs)
        if sd == 0:
            return 0.0, None
        t = md / (sd / math.sqrt(len(diffs)))
        return t, None


def _cohens_d_paired(diffs: list[float]) -> float | str | None:
    if len(diffs) < 2:
        return None
    md = mean(diffs)
    sd = pstdev(diffs)
    if sd == 0:
        return "not computable due to zero variance"
    return md / sd


def _bootstrap_paired_mean_diff_ci(diffs: list[float], *, n_boot: int = 8000, seed: int = 42) -> tuple[float | None, float | None]:
    """Bootstrap mean of paired differences by resampling paired trial indices with replacement."""
    n = len(diffs)
    if n < 2:
        if n == 1:
            return float(diffs[0]), float(diffs[0])
        return None, None
    arr = np.asarray(diffs, dtype=float)
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        means[i] = float(arr[idx].mean())
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def _rel_path_or_abs(p: Path) -> Path:
    try:
        return p.relative_to(ROOT)
    except ValueError:
        return p


def review_one_report(client: OpenAI, *, model: str, report_text: str) -> dict[str, Any]:
    user_msg = "## Report text to review\n\n" + report_text
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": REVIEWER_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
    )
    content = completion.choices[0].message.content or ""
    payload = _parse_review_json(content)
    _validate_scores(payload)
    return payload


def _deterministic_qc_prefers_grounded(
    baseline_sub: pd.DataFrame, grounded_sub: pd.DataFrame
) -> tuple[bool | None, bool]:
    """
    Estimate whether deterministic QC favors grounded over baseline using available columns.
    Returns (prefers_grounded_or_None, context_available).

    Prefer mean validity_score_0_100 when present; tie-break using mean passed_absolute_validity if available.
    """
    has_vs = (
        len(baseline_sub) > 0
        and len(grounded_sub) > 0
        and "validity_score_0_100" in baseline_sub.columns
        and "validity_score_0_100" in grounded_sub.columns
    )

    def _mean_pass(sr: pd.Series) -> float | None:
        if sr.empty:
            return None
        mask = sr.notna()
        if not mask.any():
            return None
        vals = sr[mask]

        def _as_bool_series(s: pd.Series) -> pd.Series:
            if s.dtype == bool:
                return s
            sl = s.astype(str).str.lower().str.strip()
            return sl.isin(["true", "1", "yes"])

        b = _as_bool_series(vals)
        return float(b.mean())

    if has_vs:
        vb = baseline_sub["validity_score_0_100"].astype(float).mean()
        vg = grounded_sub["validity_score_0_100"].astype(float).mean()
        if not (math.isnan(vb) or math.isnan(vg)):
            if vg > vb:
                return True, True
            if vg < vb:
                return False, True

    pass_col = "passed_absolute_validity"
    has_pass = pass_col in baseline_sub.columns and pass_col in grounded_sub.columns
    if has_pass:
        pb = _mean_pass(baseline_sub[pass_col])
        pg = _mean_pass(grounded_sub[pass_col])
        if pb is not None and pg is not None:
            if pg > pb:
                return True, True
            if pg < pb:
                return False, True

    ctx = bool(has_vs or has_pass)
    return None, ctx


def _build_interpretation(
    mean_diffs: dict[str, float],
    *,
    overall_quality_key: str = "overall_quality",
    deterministic_grounded_better: bool | None,
    deterministic_context_available: bool,
    expected_dimension_labels: tuple[str, ...],
) -> str:
    """
    Data-driven prose for qualitative AI comparisons.
    Convention: differences are grounded − baseline; positive favors grounded narrative scores.
    """
    eps = 1e-9

    def finite_diffs_only() -> dict[str, float]:
        return {k: float(v) for k, v in mean_diffs.items() if isinstance(v, (int, float)) and math.isfinite(float(v))}

    fds = finite_diffs_only()
    labels_for_all_five_check = tuple(expected_dimension_labels)
    _exp_set = set(labels_for_all_five_check)
    has_all_five = bool(labels_for_all_five_check) and _exp_set <= set(fds.keys()) and len(fds) == len(_exp_set)

    sanity = (
        "**Sign convention:** positive differences favor **grounded** narrative scores; negative differences favor "
        "**baseline** narrative scores."
    )

    if not fds:
        return (
            sanity
            + "\n\nInsufficient non-missing qualitative mean differences were available under this sign convention "
            "(for example missing reviews). Narrative interpretations are deferred."
            + "\n\nDeterministic QC evaluates factual/structural grounding against the retrieval payload; "
            "AI qualitative review evaluates perceived narrative clarity and usefulness **only**. "
            "They are complementary and not interchangeable.\n\n"
            "AI qualitative review is supplemental and inherently subjective.\n\n"
            "Synthetic educational data limits external generalization."
        )

    oq_diff = fds.get(overall_quality_key)

    favored_g = [dim for dim, d in fds.items() if d > eps]
    favored_b = [dim for dim, d in fds.items() if d < -eps]
    neutral_dims = [dim for dim in fds.keys() if dim not in favored_g + favored_b]

    _n = len(fds)
    all_pos = has_all_five and all(float(fds[lab]) > eps for lab in labels_for_all_five_check)
    all_neg = has_all_five and all(float(fds[lab]) < -eps for lab in labels_for_all_five_check)

    parts = [sanity, ""]

    if oq_diff is not None:
        ov = round(oq_diff, 3)
        if abs(ov) < eps:
            parts.append(
                f"The overall quality mean difference was **{ov:+.3f}** points on a 1–5 scale "
                "(grounded minus baseline mean), implying no practical separation on perceived overall narrative quality "
                "between prompts in aggregate."
            )
        elif oq_diff > eps:
            parts.append(
                f"The overall quality mean difference was **{ov:+.3f}** points on a 1–5 scale (grounded minus baseline "
                "mean scores), meaning **grounded reports scored higher** than baseline reports on perceived overall narrative "
                "quality across this sample."
            )
        elif oq_diff < -eps:
            parts.append(
                f"The overall quality mean difference was **{ov:+.3f}** points on a 1–5 scale (grounded minus baseline "
                "mean scores), meaning **grounded reports scored lower** than baseline reports on perceived overall narrative "
                "quality across this sample."
            )
        parts.append("")

    if all_pos:
        parts.append(
            "Across **all five** AI qualitative dimensions, **mean differences were strictly positive**, so **grounded** "
            "reports scored higher than **baseline** on average (clarity, clinical usefulness, coherence, completeness, and "
            "overall quality)."
        )
    elif all_neg:
        parts.append(
            "Across **all five** AI qualitative dimensions, **mean differences were strictly negative**, so **baseline** "
            "reports scored higher than **grounded** on average."
        )
    else:
        if has_all_five:
            parts.append("Results across dimensions were **mixed** under the grounded − baseline sign convention:")
        else:
            parts.append(
                "Fewer than five dimensions had finite mean differences summarized here, so sweeping “all five dimensions” "
                "language is withheld. Signs for available dimensions:"
            )
        if favored_g:
            parts.append(
                "- Dimensions where **mean differences favored grounded** (positive Δ): "
                + ", ".join(f"**{d}**" for d in sorted(favored_g))
                + "."
            )
        if favored_b:
            parts.append(
                "- Dimensions where **mean differences favored baseline** (negative Δ): "
                + ", ".join(f"**{d}**" for d in sorted(favored_b))
                + "."
            )
        if neutral_dims:
            parts.append("- Dimensions roughly **neutral** near zero Δ: " + ", ".join(sorted(neutral_dims)) + ".")
    parts.append("")

    det_lines = [
        "Deterministic QC evaluates **factual/structural grounding** (for example fidelity to cohort counts and required "
        "sections). AI qualitative review evaluates **narrative quality only**. These lenses are complementary and "
        "**not interchangeable**.",
    ]

    tradeoff_sentence = (
        "**This suggests a tradeoff between stricter grounding/numeric fidelity and perceived narrative readability, clarity, "
        "or usefulness** — deterministic structure can impose constraints that make prose feel tighter or less free-flowing "
        "to reviewers even when grounding improves validity checks."
    )

    if deterministic_context_available and deterministic_grounded_better is True:
        parts.append(
            "Using the available deterministic columns in this file, **grounded reports performed better under deterministic QC** "
            "(for example higher mean validity scores or higher pass-through on strict deterministic checks)."
        )
        parts.append("")
        if all_neg or (oq_diff is not None and oq_diff < -eps):
            parts.append(
                "Those deterministic findings therefore **qualitatively diverge from the narrative rubric summarized above**: "
                "scores can disagree because rubrics measure **different constructs**."
            )
            parts.append(tradeoff_sentence)
        parts.append("")
        parts.extend(det_lines)
    elif deterministic_context_available and deterministic_grounded_better is False:
        parts.append(
            "Deterministic aggregates in this file **do not show grounded strictly outperforming baseline** under the heuristic "
            "comparison used earlier (means and/or strict pass-rate gaps), which can make deterministic and qualitative signals "
            "easier to interpret jointly than when they diverge sharply."
        )
        parts.extend(["", *det_lines])
    elif deterministic_context_available and deterministic_grounded_better is None:
        parts.append(
            "Deterministic columns were present but the **direction of advantage was ambiguous** (for example ties on mean validity "
            "and strict pass-rate comparisons)."
        )
        parts.extend(["", *det_lines])
    else:
        parts.extend(det_lines)

    parts.append("")
    parts.append(
        "**Caution:** AI qualitative review is supplemental and inherently subjective — it must not substitute for "
        "deterministic validation.\n\n"
        "**Scope:** Synthetic educational data limits external generalization."
    )
    return "\n".join(parts)


def build_output_row(
    input_row: pd.Series,
    *,
    ai: dict[str, Any] | None,
    ai_available: bool,
    ai_error: str,
) -> dict[str, Any]:
    row = input_row.astype(object).where(pd.notnull(input_row), None).to_dict()
    ai_strengths = json.dumps(ai["strengths"], ensure_ascii=False) if ai else "[]"
    ai_weaknesses = json.dumps(ai["weaknesses"], ensure_ascii=False) if ai else "[]"
    row.update(
        {
            "ai_clarity": ai["clarity"] if ai else None,
            "ai_clinical_usefulness": ai["clinical_usefulness"] if ai else None,
            "ai_coherence": ai["coherence"] if ai else None,
            "ai_completeness": ai["completeness"] if ai else None,
            "ai_overall_quality": ai["overall_quality"] if ai else None,
            "ai_strengths_json": ai_strengths,
            "ai_weaknesses_json": ai_weaknesses,
            "ai_justification": ai["justification"] if ai else "",
            "ai_review_available": ai_available,
            "ai_review_error": ai_error,
        }
    )
    return row


def summarize_results(
    *,
    df: pd.DataFrame,
    input_path: Path,
    output_path: Path,
    summary_path: Path,
    model_used: str,
) -> str:
    dim_cols = [
        "ai_clarity",
        "ai_clinical_usefulness",
        "ai_coherence",
        "ai_completeness",
        "ai_overall_quality",
    ]
    dim_labels = {
        "ai_clarity": "clarity",
        "ai_clinical_usefulness": "clinical_usefulness",
        "ai_coherence": "coherence",
        "ai_completeness": "completeness",
        "ai_overall_quality": "overall_quality",
    }

    def _as_bool_avail(s: pd.Series) -> pd.Series:
        if s.dtype == bool:
            return s
        sl = s.astype(str).str.lower().str.strip()
        return sl.isin(["true", "1", "yes"])

    reviewed = df[_as_bool_avail(df["ai_review_available"])].copy()
    reviewed["mode"] = reviewed["mode"].astype(str).str.lower().str.strip()

    n_base = int((reviewed["mode"] == "baseline").sum())
    n_grnd = int((reviewed["mode"] == "grounded").sum())

    sub_b = reviewed[reviewed["mode"] == "baseline"][["trial_id"] + dim_cols].copy()
    sub_g = reviewed[reviewed["mode"] == "grounded"][["trial_id"] + dim_cols].copy()
    sub_b.columns = ["trial_id"] + [f"{c}_b" for c in dim_cols]
    sub_g.columns = ["trial_id"] + [f"{c}_g" for c in dim_cols]
    merged_pair = sub_b.merge(sub_g, on="trial_id", how="inner")
    n_paired = len(merged_pair)

    lines: list[str] = [
        "# AI qualitative review — statistical summary",
        "",
        "**This AI qualitative review was conducted offline on already-generated reports and does not "
        "modify the app, deterministic validator, or report generation pipeline.**",
        "",
        "## 1) Overview",
        "",
        f"- **Input CSV:** `{input_path}`",
        f"- **Output CSV:** `{output_path}`",
        f"- **Model:** `{model_used}`",
        f"- **Reports with successful AI review:** {len(reviewed)}",
        f"- **Baseline rows reviewed:** {n_base}",
        f"- **Grounded rows reviewed:** {n_grnd}",
        f"- **Paired trials (both modes reviewed):** {n_paired}",
        "",
    ]

    # Mean by mode + difference table
    lines.extend(["## 2) Mean AI qualitative scores by mode", "", "| Dimension | Baseline mean | Grounded mean | Difference (grounded − baseline) |", "|---|---:|---:|---:|"])
    baseline_sub = reviewed[reviewed["mode"] == "baseline"]
    grounded_sub = reviewed[reviewed["mode"] == "grounded"]
    mean_qual_diff_by_label: dict[str, float] = {}
    for c in dim_cols:
        bm = baseline_sub[c].astype(float).mean() if len(baseline_sub) else float("nan")
        gm = grounded_sub[c].astype(float).mean() if len(grounded_sub) else float("nan")
        diff = gm - bm if not (math.isnan(bm) or math.isnan(gm)) else float("nan")
        label = dim_labels[c]
        if isinstance(diff, (int, float)) and math.isfinite(float(diff)):
            mean_qual_diff_by_label[label] = float(diff)
        lines.append(
            f"| {label} | {bm:.3f} | {gm:.3f} | {diff:.3f} |"
        )
    lines.append("")

    det_prefers_g, det_ctx_avail = _deterministic_qc_prefers_grounded(baseline_sub, grounded_sub)

    # Paired analysis
    lines.extend(
        ["## 3) Paired statistical comparisons (by trial_id)", "", "Paired difference = grounded − baseline for each dimension.", ""]
    )

    if n_paired < 2:
        lines.append("_Insufficient paired trials for paired inference (need at least 2)._")
    else:
        lines.extend(
            [
                "| Dimension | n paired | Baseline mean | Grounded mean | Mean diff | Paired t | p-value | Cohen's d (paired) | Bootstrap 95% CI (mean diff) |",
                "|---|---:|---:|---:|---:|---:|---:|---|---|",
            ]
        )
        for c in dim_cols:
            bv_col = f"{c}_b"
            gv_col = f"{c}_g"
            bvals = merged_pair[bv_col].astype(float).tolist()
            gvals = merged_pair[gv_col].astype(float).tolist()
            diffs = [g - b for g, b in zip(gvals, bvals)]
            n_p = len(diffs)
            if n_p < 2:
                continue
            bbar = mean(bvals)
            gbar = mean(gvals)
            mdiff = mean(diffs)
            t_stat, p_val = _paired_t_on_diffs(diffs)
            d_cohen = _cohens_d_paired(diffs)
            lo, hi = _bootstrap_paired_mean_diff_ci(diffs, n_boot=8000, seed=42)
            p_str = f"{p_val:.4g}" if p_val is not None else "n/a (requires scipy.stats.ttest_1samp)"
            t_str = f"{t_stat:.4f}" if t_stat is not None else "n/a"
            if isinstance(d_cohen, str):
                d_str = d_cohen
            elif d_cohen is None:
                d_str = "n/a"
            else:
                d_str = f"{d_cohen:.4f}"
            ci_str = "n/a"
            if lo is not None and hi is not None:
                ci_str = f"[{lo:.4f}, {hi:.4f}]"
            lines.append(
                f"| {dim_labels[c]} | {n_p} | {bbar:.3f} | {gbar:.3f} | {mdiff:.3f} | {t_str} | {p_str} | {d_str} | {ci_str} |"
            )
        lines.append("")

    # Deterministic QC relationship
    lines.extend(["## 4) Relationship to deterministic QC", ""])
    if "validity_score_0_100" in reviewed.columns and "ai_overall_quality" in reviewed.columns:
        vv = reviewed["validity_score_0_100"].astype(float)
        aq = reviewed["ai_overall_quality"].astype(float)
        mask = vv.notna() & aq.notna()
        if mask.sum() >= 2 and float(vv[mask].std()) > 0 and float(aq[mask].std()) > 0:
            corr = float(vv[mask].corr(aq[mask]))
            lines.append(f"- Pearson correlation (**ai_overall_quality** vs **validity_score_0_100**): **{corr:.3f}**")
        else:
            lines.append("- Pearson correlation could not be computed (insufficient variance or rows).")
    else:
        lines.append("- Missing `validity_score_0_100` or `ai_overall_quality` columns for correlation.")
    lines.append("")
    if "passed_absolute_validity" in reviewed.columns:
        reviewed = reviewed.copy()

        def _as_bool_series(s: pd.Series) -> pd.Series:
            if s.dtype == bool:
                return s
            sl = s.astype(str).str.lower().str.strip()
            return sl.isin(["true", "1", "yes"])

        pb = _as_bool_series(reviewed["passed_absolute_validity"])
        reviewed["_pass_det"] = pb
        lines.append("| Deterministic pass | Mean ai_overall_quality | n |")
        lines.append("|---|:---:|---:|")
        for val, lbl in [(True, "passed"), (False, "failed")]:
            sub = reviewed[reviewed["_pass_det"] == val]
            if sub.empty:
                continue
            m = float(sub["ai_overall_quality"].astype(float).mean())
            lines.append(f"| {lbl} | {m:.3f} | {len(sub)} |")
        lines.append("")
    lines.extend(
        [
            "Deterministic validation measures factual/structural grounding against the retrieval payload; "
            "AI qualitative review measures perceived narrative clarity and usefulness **only**. "
            "These are complementary, not interchangeable.",
            "",
        ]
    )

    # Themes
    lines.extend(["## 5) Qualitative themes", ""])

    def _collect_strings(series: pd.Series) -> Counter:
        ctr: Counter[str] = Counter()
        for cell in series:
            if pd.isna(cell) or cell == "":
                continue
            try:
                arr = json.loads(str(cell))
            except json.JSONDecodeError:
                continue
            if isinstance(arr, list):
                for x in arr:
                    if isinstance(x, str) and x.strip():
                        ctr[x.strip()] += 1
        return ctr

    for mode_name in ("baseline", "grounded"):
        sub_m = reviewed[reviewed["mode"] == mode_name]
        sc = _collect_strings(sub_m["ai_strengths_json"])
        wc = _collect_strings(sub_m["ai_weaknesses_json"])
        lines.append(f"### {mode_name.title()} — strengths (exact string counts)")
        lines.append("")
        top_s = sc.most_common(10)
        if not top_s:
            lines.append("_No aggregated strengths strings._")
        else:
            for s, ct in top_s:
                lines.append(f"- `{s}` — {ct}")
        lines.append("")
        lines.append(f"### {mode_name.title()} — weaknesses (exact string counts)")
        lines.append("")
        top_w = wc.most_common(10)
        if not top_w:
            lines.append("_No aggregated weakness strings._")
        else:
            for s, ct in top_w:
                lines.append(f"- `{s}` — {ct}")
        lines.append("")

    interpretation_md = _build_interpretation(
        mean_qual_diff_by_label,
        deterministic_grounded_better=det_prefers_g,
        deterministic_context_available=det_ctx_avail,
        expected_dimension_labels=tuple(dim_labels[c] for c in dim_cols),
    )

    lines.extend(
        [
            "## 6) Interpretation",
            "",
            interpretation_md.strip(),
            "",
            "## 7) Output files",
            "",
            f"- `{output_path}` — row-level AI qualitative scores and JSON-coded themes",
            f"- `{summary_path}` — this summary document",
            "",
        ]
    )

    text = "\n".join(lines)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(text, encoding="utf-8")
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline AI qualitative review for QC CSV report rows.")
    parser.add_argument("--input", type=str, required=True, help="Input QC CSV path (relative to HW2/).")
    parser.add_argument("--output", type=str, required=True, help="Output CSV for AI-augmented rows.")
    parser.add_argument("--summary", type=str, required=True, help="Markdown summary path.")
    parser.add_argument("--model", type=str, default=None, help="OpenAI model (default env chain).")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of NEW reviews this run.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Pause between API calls.")
    parser.add_argument("--resume", action="store_true", help="Skip rows already reviewed in output CSV.")
    parser.add_argument("--force", action="store_true", help="Delete existing output CSV before starting.")
    args = parser.parse_args()

    _require_api_key()
    input_path = (ROOT / args.input).resolve()
    output_path = (ROOT / args.output).resolve()
    summary_path = (ROOT / args.summary).resolve()

    if not input_path.is_file():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if output_path.is_file() and not args.resume and not args.force:
        print(
            f"ERROR: Output file already exists: {output_path}\n"
            "Use --force to replace it from scratch or --resume to reuse successful reviews.",
            file=sys.stderr,
        )
        sys.exit(1)

    model_used = args.model or _default_model()
    df_in = pd.read_csv(input_path)
    missing = [c for c in REQ_COLS if c not in df_in.columns]
    if missing:
        print(f"ERROR: Input CSV missing required columns: {missing}", file=sys.stderr)
        sys.exit(1)

    state: dict[tuple[Any, str], dict[str, Any]] = {}
    n_skip_successful = 0

    if args.force and output_path.is_file():
        output_path.unlink()
        print("--force: removed existing output CSV.", flush=True)

    if output_path.is_file() and args.resume and not args.force:
        df_prev = pd.read_csv(output_path)
        for _, r in df_prev.iterrows():
            k = _row_key(r)
            state[k] = r.astype(object).where(pd.notnull(r), None).to_dict()
            if bool(state[k].get("ai_review_available", False)):
                n_skip_successful += 1
        print(f"--resume: imported {len(state)} rows from output; successful reviews: {n_skip_successful}.", flush=True)

    def snapshot_output() -> None:
        ordered: list[dict[str, Any]] = []
        for _, rin in df_in.iterrows():
            k = _row_key(rin)
            cell = state.get(k)
            if cell is not None:
                ordered.append(cell)
            else:
                ordered.append(
                    build_output_row(
                        rin,
                        ai=None,
                        ai_available=False,
                        ai_error="Pending (not yet reviewed in this run).",
                    )
                )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(ordered).to_csv(output_path, index=False)

    client = OpenAI()
    new_completed = 0
    total = len(df_in)

    for idx, (_, input_row) in enumerate(df_in.iterrows()):
        key = _row_key(input_row)
        if args.resume:
            prev = state.get(key)
            if prev and bool(prev.get("ai_review_available")):
                print(f"[{idx + 1}/{total}] SKIP (resume) trial_id={key[0]} mode={key[1]}", flush=True)
                snapshot_output()
                if args.sleep_seconds and args.sleep_seconds > 0:
                    time.sleep(args.sleep_seconds)
                continue

        if args.limit is not None and new_completed >= args.limit:
            msg = "Skipped: --limit reached before this row was reviewed."
            state[key] = build_output_row(input_row, ai=None, ai_available=False, ai_error=msg)
            print(f"[{idx + 1}/{total}] LIMIT skip trial_id={key[0]} mode={key[1]}", flush=True)
            snapshot_output()
            if args.sleep_seconds and args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
            continue

        try:
            text = str(input_row["report_text"])
            if not text.strip():
                raise ValueError("Empty report_text")
            payload = review_one_report(client, model=model_used, report_text=text)
            state[key] = build_output_row(input_row, ai=payload, ai_available=True, ai_error="")
            new_completed += 1
            print(f"[{idx + 1}/{total}] OK trial_id={key[0]} mode={key[1]} (new successes this run: {new_completed})", flush=True)
        except Exception as e:
            err_msg = str(e)
            state[key] = build_output_row(
                input_row,
                ai=None,
                ai_available=False,
                ai_error=err_msg[:2000],
            )
            print(f"[{idx + 1}/{total}] ERROR trial_id={key[0]} mode={key[1]}: {err_msg}", flush=True)

        snapshot_output()
        if args.sleep_seconds and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    df_final = pd.read_csv(output_path)

    summarize_results(
        df=df_final,
        input_path=_rel_path_or_abs(input_path),
        output_path=_rel_path_or_abs(output_path),
        summary_path=_rel_path_or_abs(summary_path),
        model_used=model_used,
    )
    print(f"Wrote summary: {summary_path}", flush=True)
    print(f"Done. Output rows: {len(df_final)}", flush=True)


if __name__ == "__main__":
    main()
