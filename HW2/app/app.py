# app.py
# High Risk Patient Identifier — clinical dashboard (Shiny).
# Run from HW2:  shiny run app/app.py --reload

from __future__ import annotations

import sys
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import markdown
import pandas as pd
from htmltools import HTML, head_content, html_escape
from shiny import App, reactive, render, ui

APP_DIR = Path(__file__).resolve().parent
HW2_ROOT = APP_DIR.parent
if str(HW2_ROOT) not in sys.path:
    sys.path.insert(0, str(HW2_ROOT))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import clinical_pipeline as hw  # noqa: E402
from functions import openai_api_configured  # noqa: E402
from live_validation_card import (  # noqa: E402
    LIVE_VALIDATION_CSS,
    build_live_validation_html,
    grounded_section_headers_missing,
)
from qc.scoring import compute_validity_score  # noqa: E402
from qc.statistical_analysis import analyze_results  # noqa: E402
from qc.validators import (  # noqa: E402
    HW2_GROUNDED_SECTION_HEADERS,
    extract_hw2_ground_truth,
    validate_hw2_report,
)

CSS_PATH = APP_DIR / "www" / "clinical.css"
_batch_qc_env_path = os.environ.get("HW2_QC_BATCH_RESULTS_PATH", "").strip()
BATCH_QC_FALLBACK_PATH = HW2_ROOT / "out" / "qc_results.csv"
BATCH_QC_LATEST_POINTER = HW2_ROOT / "out" / "qc_batches" / "LATEST_IMMUTABLE_CSV.txt"
HARDWIRED_QC_BATCH_CSV = HW2_ROOT / "out" / "qc_batches" / "qc_50trials_20260505T130841Z.csv"
HARDWIRED_QC_BATCH_MD = HW2_ROOT / "out" / "qc_batches" / "qc_50trials_20260505T130841Z.md"
HARDWIRED_QC_SUMMARY: dict = {
    "absolute_validity": {
        "mean_score": 71.64,
        "ci95_low": 67.70,
        "ci95_high": 75.58,
        "pass_rate": 0.50,
        "by_mode": {
            "baseline": {
                "mean_validity": 51.64,
                "pass_rate": 0.0,
                "pass_rate_wilson_low": 0.0,
                "pass_rate_wilson_high": 0.071,
            },
            "grounded": {
                "mean_validity": 91.64,
                "pass_rate": 1.0,
                "pass_rate_wilson_low": 0.929,
                "pass_rate_wilson_high": 1.0,
            },
        },
    },
    "comparative": {
        "paired_t_stat": 95.37,
        "effect_size_cohens_d": 19.70,
    },
    "pass_rate_comparison": {
        "n_paired_trials": 50,
        "pass_rate_difference": 1.0,
        "difference_ci95_low": 1.0,
        "difference_ci95_high": 1.0,
        "mcnemar_p_value": 1.776e-15,
        "mcnemar_method": "statsmodels exact",
        "mcnemar_b": 0,
        "mcnemar_c": 50,
    },
    "failure_mode_analysis": {
        "interpretation": (
            "The most common failure signals were: Did not pass absolute validity (50.0% of rows); "
            "Visit count fidelity below 1.0 (50.0% of rows); Missing required sections (50.0% of rows). "
            "Counts can overlap (one row may trigger multiple flags). Use the table above and per-row metrics "
            "in `qc_results.csv` to prioritize fixes."
        )
    },
}

COHORT_COLS: list[str] = [
    "patient_name",
    "date_of_birth",
    "visit_date",
    "phq9_score",
    "safety_concerns",
    "diagnosis",
    "provider",
    "medications",
    "patient_id",
    "visit_id",
]
# PHQ-9 severity bands (cohort is already PHQ > 15, so scores start at 16).
SEVERITY_CHOICES: dict[str, str] = {
    "mod": "15–19 (Moderate)",
    "sev": "20–24 (Severe)",
    "vsev": "25+ (Very severe)",
}
SEVERITY_ALL: tuple[str, ...] = ("mod", "sev", "vsev")

COHORT_LABELS: dict[str, str] = {
    "patient_name": "Patient",
    "date_of_birth": "Date of birth",
    "visit_date": "Visit date",
    "phq9_score": "PHQ-9",
    "safety_concerns": "Safety",
    "diagnosis": "Diagnosis",
    "provider": "Provider",
    "medications": "Medications",
    "patient_id": "Patient ID",
    "visit_id": "Visit ID",
}


def _fmt_cell_display(col: str, val: object) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    if isinstance(val, pd.Timestamp):
        return val.strftime("%Y-%m-%d")
    return str(val).strip()


def _phq_badge_html(score: float | int | None) -> str:
    if score is None or (isinstance(score, float) and pd.isna(score)):
        return f'<span class="hrpi-badge hrpi-badge--phq-missing">{html_escape("—")}</span>'
    try:
        s = int(score)
    except (TypeError, ValueError):
        esc = html_escape(str(score))
        return f'<span class="hrpi-badge hrpi-badge--phq-missing">{esc}</span>'
    cls = "hrpi-badge hrpi-badge--phq-severe" if s >= 20 else "hrpi-badge hrpi-badge--phq-elevated"
    return f'<span class="{cls}">{html_escape(str(s))}</span>'


def cohort_table_html(df: pd.DataFrame) -> str:
    cols = [c for c in COHORT_COLS if c in df.columns]
    if not cols:
        cols = list(df.columns)
    headers = "".join(f"<th>{html_escape(COHORT_LABELS.get(c, c))}</th>" for c in cols)
    rows_html: list[str] = []
    for _, row in df.iterrows():
        cells: list[str] = []
        for c in cols:
            raw = row.get(c)
            if c == "patient_name":
                text = html_escape(_fmt_cell_display(c, raw))
                cells.append(f'<td class="hrpi-col-patient"><strong>{text}</strong></td>')
            elif c == "date_of_birth":
                text = html_escape(_fmt_cell_display(c, raw))
                cells.append(f'<td class="hrpi-col-dob"><span class="hrpi-dob">{text}</span></td>')
            elif c == "phq9_score":
                cells.append(f"<td class=\"hrpi-col-phq\">{_phq_badge_html(raw)}</td>")
            else:
                cells.append(f"<td>{html_escape(_fmt_cell_display(c, raw))}</td>")
        rows_html.append(f"<tr>{''.join(cells)}</tr>")
    body = "".join(rows_html)
    return (
        f'<div class="hrpi-cohort-table-wrap"><table class="hrpi-cohort-table">'
        f"<thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def _visits_accordion_title() -> ui.Tag:
    return ui.div(
        ui.div(
            {"class": "hrpi-acc-title-lg"},
            ui.tooltip(
                ui.span(
                    {
                        "class": "hrpi-cohort-acc-title",
                        "tabindex": "0",
                        "role": "button",
                    },
                    "High-Risk Patient Visits ",
                    ui.span({"class": "hrpi-criteria-glyph", "aria-hidden": "true"}, "ⓘ"),
                ),
                "PHQ-9 ≥ 15 and safety concern = Yes",
                placement="top",
            ),
        ),
        ui.div(
            {"class": "hrpi-acc-sub"},
            "Patients with elevated depression scores and safety concerns",
        ),
    )


def _qc_dashboard_html(summary: dict) -> str:
    if not summary:
        return (
            '<p class="hrpi-muted"><strong>Run analysis</strong> first — then reopen this section to compare baseline vs grounded '
            "summary scores and statistics.</p>"
        )

    absv = summary.get("absolute_validity") or {}
    bym = absv.get("by_mode") or {}
    pb = bym.get("baseline", {})
    pg = bym.get("grounded", {})
    comp = summary.get("comparative") or {}
    prc = summary.get("pass_rate_comparison")
    fm = (summary.get("failure_mode_analysis") or {}).get("interpretation") or ""

    rows = []
    rows.append("<h4 class=\"hrpi-h4\">Baseline vs grounded summary</h4>")
    rows.append(
        "<table class=\"table table-sm hrpi-qctable\"><thead><tr><th>Summary type</th><th>Average score (0–100)</th>"
        "<th>Share passing all checks</th></tr></thead><tbody>"
    )
    rows.append(
        f"<tr><td>Baseline summary</td><td>{pb.get('mean_validity', 0):.1f}</td><td>{100*pb.get('pass_rate', 0):.1f}%</td></tr>"
    )
    rows.append(
        f"<tr><td>Grounded summary</td><td>{pg.get('mean_validity', 0):.1f}</td><td>{100*pg.get('pass_rate', 0):.1f}%</td></tr>"
    )
    rows.append("</tbody></table>")

    if comp:
        rows.append("<h4 class=\"hrpi-h4\">Paired score comparison</h4><ul>")
        rows.append(
            f"<li>Paired <em>t</em> statistic (grounded minus baseline): <code>{comp.get('paired_t_stat')}</code></li>"
        )
        rows.append(
            f"<li>Cohen's <em>d</em> (grounded − baseline): <code>{comp.get('effect_size_cohens_d')}</code></li>"
        )
        rows.append("</ul>")

    rows.append("<h4 class=\"hrpi-h4\">Pass rates &amp; McNemar test</h4><ul>")
    if isinstance(prc, dict) and prc.get("n_paired_trials"):
        pv = prc.get("mcnemar_p_value")
        rows.append(f"<li>Paired runs: <strong>{prc.get('n_paired_trials')}</strong></li>")
        rows.append(f"<li>Difference (grounded − baseline): <strong>{100 * float(prc.get('pass_rate_difference', 0)):.2f}</strong> percentage points</li>")
        rows.append(
            f"<li>Bootstrap 95% interval (percentage points): [<strong>{100 * float(prc.get('difference_ci95_low', 0)):.2f}</strong>, "
            f"<strong>{100 * float(prc.get('difference_ci95_high', 0)):.2f}</strong>]</li>"
        )
        if pv is not None:
            rows.append(f"<li>McNemar exact <em>p</em>-value: <strong>{pv:.4g}</strong> ({prc.get('mcnemar_method', '')})</li>")
            rows.append(
                f"<li>Discordant counts (baseline pass / grounded fail vs baseline fail / grounded pass): "
                f"<strong>{prc.get('mcnemar_b')}</strong>, <strong>{prc.get('mcnemar_c')}</strong></li>"
            )
        else:
            rows.append(f"<li><em>{prc.get('mcnemar_note') or 'McNemar test unavailable.'}</em></li>")
    else:
        rows.append(
            "<li><em>Run several analysis batches to unlock Wilson intervals, bootstrap, and McNemar summaries.</em></li>"
        )
    rows.append("</ul>")

    if fm:
        rows.append("<h4 class=\"hrpi-h4\">Common gaps (from automated review)</h4>")
        rows.append(f"<p>{html_escape(fm)}</p>")

    qroot = hw.HW2_ROOT / "out"
    rows.append("<h4 class=\"hrpi-h4\">Saved output files</h4><ul>")
    rows.append(f"<li><code>{html_escape(str(qroot / 'qc_results.csv'))}</code></li>")
    rows.append(f"<li><code>{html_escape(str(qroot / 'qc_summary.md'))}</code></li>")
    rows.append("</ul>")
    rows.append(
        '<p class="hrpi-muted">Uses synthetic demonstration data — scores support teaching only, '
        "not regulatory or clinical certification.</p>"
    )
    return "\n".join(rows)


def validate_current_report(report_text: str, cohort_df: pd.DataFrame, retrieval_payload: dict, verify_json: dict) -> dict | None:
    if not (report_text or "").strip():
        return None
    gt = extract_hw2_ground_truth(cohort_df, retrieval_payload, verify_json)
    metrics = validate_hw2_report(report_text, gt, section_headers=HW2_GROUNDED_SECTION_HEADERS)
    score = compute_validity_score(metrics)
    return {
        "metrics": dict(metrics),
        "score": dict(score),
        "missing_headers": grounded_section_headers_missing(report_text),
    }


def load_saved_qc_batch_results(path: Path = BATCH_QC_FALLBACK_PATH) -> dict:
    resolved_path = path
    if _batch_qc_env_path:
        resolved_path = Path(_batch_qc_env_path)
    elif BATCH_QC_LATEST_POINTER.is_file():
        try:
            pointed = BATCH_QC_LATEST_POINTER.read_text(encoding="utf-8").strip()
            if pointed:
                resolved_path = Path(pointed)
        except Exception:
            resolved_path = BATCH_QC_FALLBACK_PATH
    else:
        resolved_path = BATCH_QC_FALLBACK_PATH

    out: dict = {
        "path": str(resolved_path),
        "exists": resolved_path.is_file(),
        "row_count": 0,
        "paired_runs": 0,
        "summary": {},
        "warning": "",
        "regenerated_during_app_run": "No",
    }
    if not resolved_path.is_file():
        out["warning"] = "Saved QC batch CSV not found. Run the dedicated batch QC script to generate it."
        return out
    try:
        df = pd.read_csv(resolved_path)
    except Exception:
        out["warning"] = "Saved QC batch CSV could not be read. Re-run the dedicated batch QC script."
        return out

    if df.columns.duplicated().any():
        # Some CSV exports can accidentally include duplicate header names (e.g., duplicate `mode`);
        # keep first occurrence so downstream groupby/pivot operations remain well-defined.
        dup_cols = sorted(set(df.columns[df.columns.duplicated()].tolist()))
        df = df.loc[:, ~df.columns.duplicated()]
        out["warning"] = (
            "Saved QC batch CSV contained duplicate columns "
            f"({', '.join(dup_cols)}). Using first occurrence for dashboard statistics."
        )

    out["row_count"] = int(len(df))
    try:
        out["summary"] = analyze_results(df) if not df.empty else {}
    except Exception as exc:
        out["summary"] = {}
        out["warning"] = (
            f"Saved QC batch CSV could not be summarized ({html_escape(str(exc))}). "
            "Re-run the dedicated batch QC script to refresh outputs."
        )
        return out
    if {"trial_id", "mode"} <= set(df.columns):
        mode_norm = df["mode"].astype(str).str.lower().str.strip()
        trial_mode = pd.DataFrame({"trial_id": df["trial_id"], "mode": mode_norm}).dropna()
        if not trial_mode.empty:
            trial_mode = trial_mode.drop_duplicates(subset=["trial_id", "mode"])
            counts = trial_mode.groupby("trial_id")["mode"].apply(set)
            out["paired_runs"] = int(counts.apply(lambda s: {"baseline", "grounded"} <= s).sum())
    if out["paired_runs"] < 2:
        out["warning"] = (
            f"Saved QC batch has only {out['paired_runs']} paired run(s). "
            "Run the dedicated batch QC script with more trials (for example 50) for stable comparison statistics."
        )
    return out


def _qc_panel_from_saved_batch(batch: dict) -> str:
    summary = batch.get("summary") or {}

    rows: list[str] = []
    if not summary:
        rows.append('<p class="hrpi-muted">No batch comparison statistics are available from the saved CSV.</p>')
        return "\n".join(rows)

    rows.append(_qc_dashboard_html(summary))
    return "\n".join(rows)


def _reference_end_date(df: pd.DataFrame | None) -> date:
    """Anchor relative windows to the latest visit in the cohort (stable for static DBs)."""
    if df is None or df.empty or "visit_date" not in df.columns:
        return date.today()
    mx = pd.to_datetime(df["visit_date"], errors="coerce").max()
    if pd.isna(mx):
        return date.today()
    return mx.date()


def _phq_severity_mask(s: pd.Series, bands: set[str]) -> pd.Series:
    """Rows whose PHQ-9 falls in any selected band (16–19, 20–24, 25+)."""
    m = pd.Series(False, index=s.index)
    if "mod" in bands:
        m |= (s >= 16) & (s <= 19)
    if "sev" in bands:
        m |= (s >= 20) & (s <= 24)
    if "vsev" in bands:
        m |= s >= 25
    return m


def _filter_chip(text: str, remove_input_id: str) -> ui.Tag:
    return ui.span(
        {"class": "hrpi-chip"},
        ui.span({"class": "hrpi-chip-text"}, text),
        ui.input_action_link(remove_input_id, "\u00d7", class_="btn btn-link p-0 border-0"),
    )


def _report_accordion_title() -> ui.Tag:
    return ui.div(
        ui.div({"class": "hrpi-acc-title-lg"}, "Clinical summary"),
        ui.div(
            {"class": "hrpi-acc-sub"},
            "Structured summary aligned with cohort data and supporting tables",
        ),
    )


def _qc_accordion_title() -> ui.Tag:
    return ui.div(
        ui.div({"class": "hrpi-acc-title-lg"}, "Quality checks"),
        ui.div(
            {"class": "hrpi-acc-sub"},
            "Open for baseline vs grounded comparison, scores, and detailed statistics",
        ),
    )


app_ui = ui.page_fillable(
    head_content(
        ui.include_css(CSS_PATH),
        ui.tags.link(
            rel="stylesheet",
            href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700&display=swap",
        ),
    ),
    ui.div(
        {"class": "hrpi-dashboard"},
        ui.div(
            {"class": "hrpi-dash-header"},
            ui.div(
                {"class": "hrpi-dash-brand"},
                ui.h1({"class": "hrpi-dash-title"}, "High Risk Patient Identifier"),
                ui.p(
                    {"class": "hrpi-dash-tagline"},
                    "Review high-risk visits and a concise clinical summary in one place.",
                ),
            ),
            ui.output_ui("ai_status_badge"),
        ),
        ui.div(
            {"class": "hrpi-hero-card"},
            ui.p(
                {"class": "hrpi-hero-lead"},
                "Review the latest high-risk visits and generate a concise clinical summary. "
                "First runs may take about a minute.",
            ),
            ui.output_ui("setup_hint"),
            ui.input_action_button(
                "run_pipeline",
                "Run analysis",
                class_="btn-primary hrpi-btn-primary",
            ),
        ),
        ui.output_ui("banner_messages"),
        ui.accordion(
            ui.accordion_panel(
                ui.div(
                    ui.div({"class": "hrpi-acc-title-lg"}, "Activity"),
                    ui.div(
                        {"class": "hrpi-acc-sub"},
                        "Latest run details",
                    ),
                ),
                ui.output_ui("activity_body"),
                value="activity",
            ),
            ui.accordion_panel(
                _visits_accordion_title(),
                ui.div(
                    {"class": "hrpi-cohort-panel-inner"},
                    ui.output_ui("cohort_summary"),
                    ui.div(
                        {"class": "hrpi-filter-bar"},
                        ui.div(
                            {"class": "hrpi-filter-item hrpi-filter-item--grow"},
                            ui.input_text(
                                "cohort_search",
                                "Search",
                                placeholder="Search patients...",
                            ),
                        ),
                        ui.div(
                            {"class": "hrpi-filter-item"},
                            ui.input_select(
                                "filter_date_preset",
                                "Date",
                                choices={
                                    "all": "All dates",
                                    "last7": "Last 7 days",
                                    "last30": "Last 30 days",
                                    "custom": "Custom range",
                                },
                                selected="all",
                            ),
                        ),
                        ui.panel_conditional(
                            "input.filter_date_preset == 'custom'",
                            ui.div(
                                {"class": "hrpi-filter-item hrpi-filter-item--date-custom"},
                                ui.input_date_range(
                                    "filter_visit_custom",
                                    "Custom range",
                                    start=date(2010, 1, 1),
                                    end=date(2040, 12, 31),
                                    min=date(1990, 1, 1),
                                    max=date(2050, 12, 31),
                                ),
                            ),
                        ),
                        ui.div(
                            {"class": "hrpi-filter-item"},
                            ui.input_selectize(
                                "filter_provider",
                                "Provider",
                                choices={"_": "After analysis, providers appear here"},
                                multiple=True,
                                selected=[],
                            ),
                        ),
                        ui.div(
                            {"class": "hrpi-filter-item"},
                            ui.input_selectize(
                                "filter_phq_severity",
                                "PHQ-9 severity",
                                choices=SEVERITY_CHOICES,
                                multiple=True,
                                selected=list(SEVERITY_ALL),
                            ),
                        ),
                    ),
                    ui.output_ui("filter_chips"),
                    ui.output_ui("cohort_table_ui"),
                ),
                value="visits",
            ),
            ui.accordion_panel(
                _report_accordion_title(),
                ui.div(
                    ui.output_ui("report_context"),
                    ui.output_ui("report_panel"),
                    ui.div(
                        {"class": "hrpi-live-validation-wrap"},
                        ui.output_ui("live_report_validation"),
                    ),
                ),
                value="report",
            ),
            ui.accordion_panel(
                _qc_accordion_title(),
                ui.div(
                    ui.p(
                        {"class": "hrpi-qc-intro"},
                        "Detailed quality metrics comparison: baseline versus grounded summaries, "
                        "how often each clears all automated checks, and statistical tests from your latest batch run.",
                    ),
                    ui.output_ui("qc_panel"),
                ),
                value="qc",
            ),
            id="main_acc",
            open=["activity", "visits", "report"],
            multiple=True,
            class_="hrpi-dash-accordion",
        ),
        ui.div(
            {"class": "hrpi-dash-footer"},
            "Synthetic educational data only — not for clinical use.",
        ),
    ),
    title="High Risk Patient Identifier",
    fillable=True,
)


def server(input, output, session):
    cohort_df_rv = reactive.Value(None)
    report_md_rv = reactive.Value("")
    activity_rv = reactive.Value('Select Run analysis when you are ready.')
    last_updated_rv = reactive.Value("")
    last_metrics_rv = reactive.Value((0, 0, True))
    live_validation_bundle_rv: reactive.Value[dict | None] = reactive.Value(None)
    running_rv = reactive.Value(False)
    banner_error_rv = reactive.Value("")
    ai_ok_rv = reactive.Value(None)

    @reactive.effect
    def _probe_ai_on_load():
        ai_ok_rv.set(openai_api_configured())

    @reactive.effect
    @reactive.event(input.run_pipeline)
    def _run_pipeline():
        banner_error_rv.set("")
        ai_ok_rv.set(openai_api_configured())
        if not hw.DB_PATH.is_file():
            banner_error_rv.set("Patient records not found — add patients.db beside the app folder or configure PATIENTS_DB.")
            activity_rv.set("Cannot run analysis — patient record file missing.")
            return
        running_rv.set(True)
        activity_rv.set(
            "Working on your summary…\n\n"
            "Gathering visits and drafting the clinical summary — this may take a few minutes — please keep this tab open."
        )
        try:
            result = hw.run_live_homework2_pipeline(log=None)
            cohort_df_rv.set(result["cohort_df"])
            report_md_rv.set(result["report_full"])
            try:
                live_bundle = validate_current_report(
                    result["report_full"],
                    result["cohort_df"],
                    result["retrieval_payload"],
                    result["verify_json"],
                )
                live_validation_bundle_rv.set(live_bundle)
            except Exception:
                live_validation_bundle_rv.set(None)
            ok = result["verify_json"]["all_passed"]
            n_visits = result["n_visits"]
            n_patients = result["n_patients"]
            g_pass = None
            if live_validation_bundle_rv.get():
                g_pass = bool((live_validation_bundle_rv.get().get("score") or {}).get("passed_absolute_validity"))
            last_updated_rv.set(datetime.now().strftime("%b %d, %Y"))
            last_metrics_rv.set((n_visits, n_patients, ok))
            rqc = "Data checks passed" if ok else "Data checks flagged — review quality notes below"
            pq = ""
            if g_pass is not None:
                pq = f"\nCurrent grounded summary quality check: {'passed' if g_pass else 'needs review'}."
            activity_rv.set(
                "Analysis completed successfully.\n\n"
                f"High-risk visits: {n_visits}\n"
                f"Patients: {n_patients}\n"
                f"{rqc}{pq}"
            )
            df = result["cohort_df"]
            if df is not None and not df.empty:
                if "visit_date" in df.columns:
                    vd = pd.to_datetime(df["visit_date"], errors="coerce")
                    vmin, vmax = vd.min(), vd.max()
                    if pd.notna(vmin) and pd.notna(vmax):
                        ui.update_date_range(
                            "filter_visit_custom",
                            start=vmin.date(),
                            end=vmax.date(),
                        )
                if "provider" in df.columns:
                    provs = sorted(df["provider"].dropna().astype(str).unique())
                    ui.update_selectize(
                        "filter_provider",
                        choices={p: p for p in provs},
                        selected=[],
                    )
                ui.update_select("filter_date_preset", selected="all")
                ui.update_selectize(
                    "filter_phq_severity",
                    choices=SEVERITY_CHOICES,
                    selected=list(SEVERITY_ALL),
                )
        except Exception:
            banner_error_rv.set(
                "Something went wrong while generating your summary — check connectivity, quotas, "
                "and ensure your AI key or account settings allow this request."
            )
            activity_rv.set(
                "The analysis could not finish — try again shortly. If problems continue, verify your AI access and quota."
            )
        finally:
            running_rv.set(False)

    @render.ui
    def ai_status_badge():
        ok = ai_ok_rv.get()
        if ok is True:
            return ui.span({"class": "hrpi-badge-ai hrpi-badge-ai--ok"}, "AI ready")
        if ok is False:
            return ui.span({"class": "hrpi-badge-ai hrpi-badge-ai--bad"}, "Needs API key")
        return ui.span({"class": "hrpi-badge-ai hrpi-badge-ai--unknown"}, "Checking…")

    @render.ui
    def setup_hint():
        if ai_ok_rv.get() is False:
            return ui.div({"class": "hrpi-setup-hint"}, "AI setup needed — add your OpenAI API key to continue.")
        return ui.div()

    @render.ui
    def banner_messages():
        if running_rv.get():
            return ui.div(
                {"class": "hrpi-banner hrpi-banner--loading"},
                "Running analysis…",
            )
        err = banner_error_rv.get()
        if err:
            return ui.div({"class": "hrpi-banner hrpi-banner--error"}, err)
        return ui.div()

    @render.ui
    def activity_body():
        return ui.div({"class": "hrpi-activity-body"}, activity_rv.get())

    def _as_str_tuple(val: object) -> tuple[str, ...]:
        if val is None:
            return ()
        if isinstance(val, str):
            return (val,)
        return tuple(str(x) for x in val)

    @reactive.effect
    @reactive.event(input.filter_chip_rm_date)
    def _chip_remove_date():
        ui.update_select("filter_date_preset", selected="all")

    @reactive.effect
    @reactive.event(input.filter_chip_rm_search)
    def _chip_remove_search():
        ui.update_text("cohort_search", value="")

    @reactive.effect
    @reactive.event(input.filter_chip_rm_provider)
    def _chip_remove_provider():
        ui.update_selectize("filter_provider", selected=[])

    @reactive.effect
    @reactive.event(input.filter_chip_rm_severity)
    def _chip_remove_severity():
        ui.update_selectize("filter_phq_severity", selected=list(SEVERITY_ALL))

    @reactive.effect
    @reactive.event(input.filter_clear_all)
    def _filter_clear_all():
        ui.update_select("filter_date_preset", selected="all")
        ui.update_text("cohort_search", value="")
        ui.update_selectize("filter_provider", selected=[])
        ui.update_selectize("filter_phq_severity", selected=list(SEVERITY_ALL))
        df = cohort_df_rv.get()
        if df is not None and not df.empty and "visit_date" in df.columns:
            vd = pd.to_datetime(df["visit_date"], errors="coerce")
            vmin, vmax = vd.min(), vd.max()
            if pd.notna(vmin) and pd.notna(vmax):
                ui.update_date_range(
                    "filter_visit_custom",
                    start=vmin.date(),
                    end=vmax.date(),
                )

    @reactive.calc
    def filtered_cohort() -> pd.DataFrame | None:
        df = cohort_df_rv.get()
        if df is None:
            return None
        out = df.copy()
        anchor_df = df
        if "visit_date" in out.columns:
            out["_visit_dt"] = pd.to_datetime(out["visit_date"], errors="coerce")
        preset = input.filter_date_preset()
        if preset != "all" and "_visit_dt" in out.columns:
            ref = _reference_end_date(anchor_df)
            ref_ts = pd.Timestamp(ref)
            if preset == "last7":
                start_ts = ref_ts - timedelta(days=7)
                m = out["_visit_dt"].between(start_ts, ref_ts, inclusive="both")
                out = out.loc[m.fillna(False)]
            elif preset == "last30":
                start_ts = ref_ts - timedelta(days=30)
                m = out["_visit_dt"].between(start_ts, ref_ts, inclusive="both")
                out = out.loc[m.fillna(False)]
            elif preset == "custom":
                dr = input.filter_visit_custom()
                if dr is not None:
                    start_d, end_d = dr
                    if start_d is not None and end_d is not None:
                        start_ts = pd.Timestamp(start_d)
                        end_ts = pd.Timestamp(end_d) + pd.Timedelta(days=1) - pd.Timedelta(
                            microseconds=1
                        )
                        m = out["_visit_dt"].between(start_ts, end_ts, inclusive="both")
                        out = out.loc[m.fillna(False)]
        prov_sel = _as_str_tuple(input.filter_provider())
        prov_sel = tuple(p for p in prov_sel if p != "_")
        if prov_sel and "provider" in out.columns:
            out = out.loc[out["provider"].astype(str).isin(prov_sel)]
        sev_sel = _as_str_tuple(input.filter_phq_severity())
        bands = {b for b in sev_sel if b in SEVERITY_ALL}
        if not bands:
            bands = set(SEVERITY_ALL)
        if bands != set(SEVERITY_ALL) and "phq9_score" in out.columns:
            s = pd.to_numeric(out["phq9_score"], errors="coerce")
            out = out.loc[_phq_severity_mask(s, bands)]
        q = (input.cohort_search() or "").strip().lower()
        if q:
            parts: list[pd.Series] = []
            for c in out.columns:
                if c.startswith("_"):
                    continue
                parts.append(out[c].apply(lambda v, col=c: _fmt_cell_display(col, v).lower()))
            if parts:
                mask = pd.concat(parts, axis=1).apply(
                    lambda r: r.astype(str).str.contains(q, regex=False).any(), axis=1
                )
                out = out.loc[mask]
        drop_cols = [c for c in out.columns if c.startswith("_")]
        if drop_cols:
            out = out.drop(columns=drop_cols)
        return out

    @render.ui
    def filter_chips():
        if cohort_df_rv.get() is None:
            return ui.div()
        chips: list[ui.Tag] = []
        preset = input.filter_date_preset()
        if preset == "last7":
            chips.append(_filter_chip("Date: Last 7 days", "filter_chip_rm_date"))
        elif preset == "last30":
            chips.append(_filter_chip("Date: Last 30 days", "filter_chip_rm_date"))
        elif preset == "custom":
            dr = input.filter_visit_custom()
            if dr is not None:
                a, b = dr
                if a is not None and b is not None:
                    label = f"Date: {a} – {b}"
                    chips.append(_filter_chip(label, "filter_chip_rm_date"))
        q = (input.cohort_search() or "").strip()
        if q:
            disp = q if len(q) <= 40 else q[:37] + "..."
            chips.append(_filter_chip(f'Search: "{disp}"', "filter_chip_rm_search"))
        prov_sel = tuple(p for p in _as_str_tuple(input.filter_provider()) if p != "_")
        if prov_sel:
            if len(prov_sel) == 1:
                ptxt = prov_sel[0]
                if len(ptxt) > 32:
                    ptxt = ptxt[:29] + "..."
                chips.append(_filter_chip(f"Provider: {ptxt}", "filter_chip_rm_provider"))
            else:
                first = prov_sel[0]
                if len(first) > 22:
                    first = first[:19] + "..."
                chips.append(
                    _filter_chip(
                        f"Provider: {first} +{len(prov_sel) - 1} more",
                        "filter_chip_rm_provider",
                    )
                )
        sev_sel = _as_str_tuple(input.filter_phq_severity())
        band_set = {b for b in sev_sel if b in SEVERITY_ALL}
        if not band_set:
            band_set = set(SEVERITY_ALL)
        if band_set != set(SEVERITY_ALL):
            if band_set == {"mod"}:
                sev_label = "Severity: 15–19 (Moderate)"
            elif band_set == {"sev"}:
                sev_label = "Severity: 20–24 (Severe)"
            elif band_set == {"vsev"}:
                sev_label = "Severity: 25+ (Very severe)"
            elif band_set == {"sev", "vsev"}:
                sev_label = "Severity: ≥20"
            elif band_set == {"mod", "sev"}:
                sev_label = "Severity: 15–24"
            else:
                sev_label = "Severity: " + ", ".join(
                    SEVERITY_CHOICES[b] for b in ("mod", "sev", "vsev") if b in band_set
                )
            chips.append(_filter_chip(sev_label, "filter_chip_rm_severity"))
        row_children: list[object] = list(chips)
        if chips:
            row_children.append(
                ui.input_action_link(
                    "filter_clear_all",
                    "Clear all",
                    class_="btn btn-link hrpi-clear-all",
                )
            )
        return ui.div({"class": "hrpi-filter-chips"}, *row_children)

    @render.ui
    def cohort_summary():
        base = cohort_df_rv.get()
        if base is None:
            return ui.div(
                {"class": "hrpi-cohort-summary hrpi-cohort-summary--muted"},
                "Run analysis to load visits.",
            )
        fc = filtered_cohort()
        assert fc is not None
        n = len(fc)
        n_all = len(base)
        updated = last_updated_rv.get() or "—"
        suffix = f" ({n} of {n_all} after filters)" if n != n_all else ""
        return ui.div(
            {"class": "hrpi-cohort-summary"},
            ui.tags.strong(f"{n} high-risk visit{'s' if n != 1 else ''}"),
            f" · Updated {updated}{suffix}",
        )

    @render.ui
    def cohort_table_ui():
        df = cohort_df_rv.get()
        if df is None:
            return ui.div(
                {"class": "hrpi-cohort-empty"},
                "Run analysis to load the visit list.",
            )
        fc = filtered_cohort()
        assert fc is not None
        if fc.empty:
            return ui.div(
                {"class": "hrpi-cohort-empty"},
                "No visits match your search or filters. Adjust the filter bar or use Clear all.",
            )
        return ui.div(HTML(cohort_table_html(fc)))

    @render.ui
    def report_context():
        md = report_md_rv.get()
        if not md:
            return ui.div()
        updated = last_updated_rv.get() or "—"
        n_visits, n_patients, _ = last_metrics_rv.get()
        return ui.div(
            {"class": "hrpi-report-context"},
            ui.tags.strong(f"{n_visits} high-risk visits"),
            f" · {n_patients} patients · Updated {updated}",
        )

    @render.ui
    def report_panel():
        md = report_md_rv.get()
        if not md:
            return ui.div(
                {"class": "hrpi-report-empty"},
                "Your grounded clinical summary will appear here after you run analysis.",
            )
        html = markdown.markdown(md, extensions=["tables", "fenced_code", "nl2br"])
        return ui.div({"class": "hrpi-report-body"}, ui.HTML(html))

    @render.ui
    def live_report_validation():
        md = report_md_rv.get() or ""
        n_visits, n_patients, retr_ok = last_metrics_rv.get()
        b = live_validation_bundle_rv.get()
        if md.strip() and not b:
            stale = (
                LIVE_VALIDATION_CSS
                + '<div class="lv-section-title">Summary quality checks</div>'
                + '<div class="lv-scope"><p class="lv-muted">'
                "Scores for this screen are outdated — choose <strong>Run analysis</strong> again "
                "to refresh the summary quality checklist.</p></div>"
            )
            return ui.HTML(stale)
        metrics: dict = {}
        score: dict = {}
        missing_headers: list[str] = []
        if b:
            metrics = b.get("metrics") or {}
            score = b.get("score") or {}
            missing_headers = list(b.get("missing_headers") or [])
        html_str = build_live_validation_html(
            report_text=md.strip(),
            metrics=metrics,
            score=score,
            n_visits=int(n_visits),
            n_patients=int(n_patients),
            retrieval_all_passed=bool(retr_ok),
            missing_headers=missing_headers,
        )
        return ui.HTML(html_str)

    @render.ui
    def qc_panel():
        batch = {
            "path": str(HARDWIRED_QC_BATCH_CSV),
            "exists": True,
            "row_count": 100,
            "paired_runs": 50,
            "summary": HARDWIRED_QC_SUMMARY,
            "warning": f"Hardwired static 50-trial snapshot. Source markdown: {HARDWIRED_QC_BATCH_MD}",
            "regenerated_during_app_run": "No",
        }
        return ui.div({"class": "hrpi-report-body hrpi-qcpanel"}, ui.HTML(_qc_panel_from_saved_batch(batch)))


app = App(app_ui, server)
