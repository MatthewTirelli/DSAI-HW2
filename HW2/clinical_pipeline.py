# clinical_pipeline.py
# Homework 2 — OpenAI tool cohort extraction, deterministic retrieval, dual-report QC flow

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

HW2_ROOT = Path(__file__).resolve().parent
if str(HW2_ROOT) not in sys.path:
    sys.path.insert(0, str(HW2_ROOT))

from functions import DEFAULT_MODEL, agent, agent_run, df_as_text  # noqa: E402 (loads repo `.env` via functions)
from qc.report_generation import write_summary_markdown  # noqa: E402
from qc.scoring import compute_validity_score  # noqa: E402
from qc.statistical_analysis import analyze_results  # noqa: E402
from qc.validators import (  # noqa: E402
    extract_hw2_ground_truth,
    section_headers_for_mode,
    validate_hw2_report,
)
from retrieval import build_cohort_retrieval_payload  # noqa: E402

MODEL = DEFAULT_MODEL
PHQ9_MIN_EXCLUSIVE = 15
LAPSED_FOLLOWUP_DAYS = 30

_env_db = os.environ.get("PATIENTS_DB")
DB_PATH = Path(_env_db).expanduser().resolve() if _env_db else HW2_ROOT / "patients.db"
OUT_DIR = HW2_ROOT / "out"
RULES_PATH = HW2_ROOT / "clinical_rag_rules.yaml"

PROMPT_BASELINE_PATH = HW2_ROOT / "qc" / "prompts" / "hw2_baseline_prompt.txt"
PROMPT_GROUNDED_PATH = HW2_ROOT / "qc" / "prompts" / "hw2_grounded_prompt.txt"

AGENT1_TOOL_NAME = "list_phq9_elevated_with_safety_concerns"
AGENT1_TOOL_CHOICE = {"type": "function", "function": {"name": AGENT1_TOOL_NAME}}

tool_list_phq9_safety = {
    "type": "function",
    "function": {
        "name": AGENT1_TOOL_NAME,
        "description": (
            "Query the local patients database and return a table of visits where "
            "PHQ-9 score is greater than 15 (16 or higher) and safety_concerns is 'Y'. "
            "Each row is one visit with patient name, DOB, visit date, scores, "
            "diagnosis, provider, and medications. Call with an empty argument object {}."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def list_phq9_elevated_with_safety_concerns(**_kwargs):
    path = str(DB_PATH.resolve())
    sql = """
    SELECT
        p.id AS patient_id,
        p.name AS patient_name,
        p.date_of_birth,
        v.id AS visit_id,
        v.visit_date,
        v.phq9_score,
        v.safety_concerns,
        v.diagnosis,
        v.provider,
        v.medications
    FROM visits v
    INNER JOIN patients p ON p.id = v.patient_id
    WHERE v.phq9_score > ?
      AND UPPER(TRIM(COALESCE(v.safety_concerns, ''))) = 'Y'
    ORDER BY v.visit_date DESC, p.id ASC;
    """
    with sqlite3.connect(path) as conn:
        df = pd.read_sql_query(sql, conn, params=(PHQ9_MIN_EXCLUSIVE,))
    return df


def load_rules() -> str:
    raw = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    return yaml.dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _coerce_tool_result_to_dataframe(result):
    if isinstance(result, pd.DataFrame):
        return result
    if isinstance(result, list) and result:
        last = result[-1]
        if isinstance(last, dict) and isinstance(last.get("output"), pd.DataFrame):
            return last["output"]
    msg = result.get("message") if isinstance(result, dict) else None
    if isinstance(msg, dict):
        tcalls = msg.get("tool_calls") or []
        if tcalls:
            out = tcalls[-1].get("output")
            if isinstance(out, pd.DataFrame):
                return out
    return None


def _tool_output_summary(obj):
    if isinstance(obj, pd.DataFrame):
        return {"kind": "dataframe", "rows": int(len(obj)), "columns": list(obj.columns)}
    return {"kind": type(obj).__name__, "preview": str(obj)[:400]}


def _write_agent1_tool_trace(model: str, result_all: dict) -> None:
    msg = result_all.get("message") or {}
    tool_calls = msg.get("tool_calls") or []
    if msg.get("_tool_recovery_from_content"):
        invocation = "content_recovery"
    elif tool_calls:
        invocation = "native_tool_calls"
    else:
        invocation = "none"

    trace_tool_calls = []
    for tc in tool_calls:
        fn_block = tc.get("function") or {}
        trace_tool_calls.append(
            {
                "name": fn_block.get("name") or tc.get("name"),
                "arguments": fn_block.get("arguments"),
                "output_summary": _tool_output_summary(tc.get("output")),
            }
        )

    preview = msg.get("content")
    if preview:
        preview = str(preview).replace("\n", " ")[:500]

    trace = {
        "model": model,
        "llm_backend": "openai",
        "expected_tool": AGENT1_TOOL_NAME,
        "invocation_path": invocation,
        "tool_calls": trace_tool_calls,
        "assistant_content_preview": preview if invocation != "native_tool_calls" else None,
    }
    (OUT_DIR / "agent1_tool_trace.json").write_text(
        json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _write_retrieval_verification_md(verify: dict) -> None:
    lines = [
        "# Retrieval verification",
        "",
        f"- **generated_at_utc:** `{verify.get('generated_at_utc')}`",
        f"- **all_passed:** {verify.get('all_passed')}",
        "",
        "| Check | Passed | Detail |",
        "|---|---:|---|",
    ]
    for c in verify.get("checks", []):
        d = c.get("detail") or ""
        lines.append(f"| `{c['name']}` | {c['passed']} | {d} |")
    (OUT_DIR / "retrieval_verification.md").write_text("\n".join(lines), encoding="utf-8")


def build_retrieval_verification(
    cohort_df: pd.DataFrame,
    payload: dict,
    db_path: str,
    lapsed_min_days: int,
) -> dict:
    checks = []
    payload_ids = set(int(x) for x in payload.get("cohort_patient_ids", []))
    if "patient_id" in cohort_df.columns:
        df_ids = set(int(x) for x in cohort_df["patient_id"].dropna().unique())
    else:
        df_ids = set()

    ok = payload_ids == df_ids
    checks.append(
        {
            "name": "cohort_patient_ids_match_df",
            "passed": bool(ok),
            "detail": None
            if ok
            else f"symmetric_diff payload↔df: {sorted(payload_ids ^ df_ids)[:20]}",
        }
    )

    prov = payload.get("provider_concentration") or {}
    ppc = int(prov.get("cohort_patient_count") or -1)
    ok2 = ppc == len(payload_ids)
    checks.append(
        {
            "name": "provider_concentration_cohort_patient_count",
            "passed": bool(ok2),
            "detail": None if ok2 else f"expected {len(payload_ids)}, got {ppc}",
        }
    )

    lf = payload.get("lapsed_followup") or {}
    n_rows = len(lf.get("retrieval_rows") or [])
    rc = int(lf.get("row_count") or -1)
    ok3 = rc == n_rows
    checks.append(
        {
            "name": "lapsed_row_count_matches_retrieval_rows",
            "passed": bool(ok3),
            "detail": None if ok3 else f"row_count={rc}, len(rows)={n_rows}",
        }
    )

    vrows = prov.get("visit_counts_by_provider") or []
    sum_by_prov = sum(int(r.get("visit_count", 0)) for r in vrows)

    if not payload_ids:
        sql_total = 0
    else:
        placeholders = ",".join("?" * len(payload_ids))
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                f"SELECT COUNT(*) FROM visits WHERE patient_id IN ({placeholders})",
                sorted(payload_ids),
            )
            sql_total = int(cur.fetchone()[0])

    ok4 = sql_total == sum_by_prov
    checks.append(
        {
            "name": "total_visits_sql_equals_sum_provider_visit_counts",
            "passed": bool(ok4),
            "detail": None
            if ok4
            else f"SQL total visits for cohort={sql_total}, sum provider visit_count={sum_by_prov}",
        }
    )

    all_passed = all(c["passed"] for c in checks)
    n_pat = cohort_df["patient_id"].nunique() if "patient_id" in cohort_df.columns else 0

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "db_path": db_path,
        "lapsed_followup_threshold_days": lapsed_min_days,
        "cohort_unique_patient_count": int(n_pat),
        "sql_total_visits_for_cohort": sql_total,
        "sum_visit_count_by_provider_rows": sum_by_prov,
        "checks": checks,
        "all_passed": all_passed,
    }


def _lapsed_row_count(payload: dict) -> int:
    lf = payload.get("lapsed_followup") or {}
    rc = lf.get("row_count")
    if rc is not None:
        return int(rc)
    return len(lf.get("retrieval_rows") or [])


def _fill_prompt(template: str, **kwargs: str) -> str:
    out = template
    for k, v in kwargs.items():
        out = out.replace(f"<<<{k}>>>", v)
    return out


def _build_user_prompt(path: Path, *, cohort_table: str, retrieval_json_str: str, rules_text: str, verify: dict, payload: dict, n_visits: int, n_patients: int) -> str:
    raw = path.read_text(encoding="utf-8")
    checks = verify.get("checks") or []
    vdetail = "; ".join(f"{c['name']}: {'PASS' if c.get('passed') else 'FAIL'}" for c in checks) or "none"
    vstatus = "passed" if verify.get("all_passed") else "flagged issues — see QC notes"
    lapsed = _lapsed_row_count(payload)
    filled = _fill_prompt(
        raw,
        RULES_BLOCK=rules_text,
        COHORT_TABLE=cohort_table,
        RETRIEVAL_JSON=retrieval_json_str,
        N_VISITS=str(n_visits),
        N_PATIENTS=str(n_patients),
        VERIFY_STATUS=vstatus,
        VERIFY_STATUS_DETAIL=vdetail,
        LAPSED_ROW_COUNT=str(lapsed),
    )
    return filled


def _report_system_role() -> str:
    return (
        "You write Markdown reports for synthetic educational dashboards. "
        "Follow the USER message exactly regarding structure and grounding. "
        "Do not fabricate PHI or undocumented clinical facts."
    )


def _score_report_row(*, trial_id: int, mode: str, report_text: str, gt: dict, runtime_s: float) -> dict:
    headers = section_headers_for_mode(mode)
    metrics = validate_hw2_report(report_text, gt, section_headers=headers)
    score = compute_validity_score(metrics, concision_score_1_5=None)
    row = {
        "trial_id": trial_id,
        "mode": mode,
        "runtime_s": runtime_s,
        "dominant_confidence_label": "neutral",
        "dominant_confidence_score": None,
        "report_text": report_text,
        **metrics,
        **score,
        "unsupported_claims": "[]",
        "confidence_misuse": json.dumps(metrics.get("confidence_misuse", []), ensure_ascii=False),
        "missing_required_elements": "[]",
        "grader_payload": "",
    }
    return row


def load_qc_reference_bundle(out_dir: Path | None = None) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Cohort from local SQL + frozen `retrieval_*.json` under `out/` — no OpenAI calls."""
    root = OUT_DIR if out_dir is None else Path(out_dir)
    cohort_df = list_phq9_elevated_with_safety_concerns()
    payload = json.loads((root / "retrieval_payload.json").read_text(encoding="utf-8"))
    verify = json.loads((root / "retrieval_verification.json").read_text(encoding="utf-8"))
    return cohort_df, payload, verify


def run_full_homework2_pipeline(log=None, qc_trials: int = 1, qc_base_seed: int | None = 42):
    """
    Agent 1 (OpenAI forced tool) → cohort → deterministic retrieval payload → Prompt A/B reports → QC.
    """
    del log

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    role1 = (
        "You are a clinical data assistant. You may ONLY satisfy requests by calling the "
        f"provided tool `{AGENT1_TOOL_NAME}`. Call it exactly once with arguments: {{}} "
        "(empty JSON object). Do not reply with prose, lists, or invented patients."
    )
    task1 = (
        "Pull every visit where PHQ-9 is above 15 and safety_concerns is Y. "
        f"Call `{AGENT1_TOOL_NAME}` with {{}}."
    )

    result1 = agent(
        messages=[{"role": "system", "content": role1}, {"role": "user", "content": task1}],
        model=MODEL,
        tools=[tool_list_phq9_safety],
        tool_choice=AGENT1_TOOL_CHOICE,
        all=True,
    )

    _write_agent1_tool_trace(MODEL, result1)
    cohort_df = _coerce_tool_result_to_dataframe(result1)

    if cohort_df is None:
        raise RuntimeError(
            "Agent 1 did not return cohort data via the tool. "
            "Confirm OPENAI_API_KEY in the repo-root `.env` (or exported), use a tools-capable model (e.g. gpt-4o-mini / gpt-4o), and retry."
        )

    msg = result1.get("message") or {}
    tool_calls = msg.get("tool_calls") or []
    invocation_path = "content_recovery" if msg.get("_tool_recovery_from_content") else (
        "native_tool_calls" if tool_calls else "unknown"
    )

    n_visits = int(len(cohort_df))
    n_patients = (
        int(cohort_df["patient_id"].nunique()) if "patient_id" in cohort_df.columns else n_visits
    )

    if tool_calls:
        last_out = tool_calls[-1].get("output")
        out_preview = _tool_output_summary(last_out)
    else:
        out_preview = {"kind": "none"}

    agent1_md_parts = [
        "# Cohort extraction record",
        "",
        "## Run metadata",
        "",
        f"- **Model:** `{MODEL}`",
        f"- **Backend:** OpenAI Chat Completions",
        f"- **Cohort function:** `{AGENT1_TOOL_NAME}`",
        f"- **Database:** `{DB_PATH}`",
        "- **Cohort rule:** PHQ-9 > 15 and `safety_concerns` = Y",
        "- **Execution trace:** `agent1_tool_trace.json`",
        "",
        "## Function-calling verification",
        "",
        f"- **Invocation path:** `{invocation_path}`",
        f"- **Tool name:** `{AGENT1_TOOL_NAME}`",
        f"- **Tool output:** `{out_preview}`",
        "",
        "## Summary",
        "",
        f"- Qualifying visits: **{n_visits}**",
        f"- Unique patients: **{n_patients}**",
        "",
        "## Full cohort (all columns)",
        "",
        df_as_text(cohort_df),
        "",
    ]
    (OUT_DIR / "agent1_cohort_findings.md").write_text("\n".join(agent1_md_parts), encoding="utf-8")

    patient_ids = [int(x) for x in cohort_df["patient_id"].dropna().unique()] if n_visits else []
    db_path_str = str(DB_PATH.resolve())
    payload = build_cohort_retrieval_payload(db_path_str, patient_ids, lapsed_min_days=LAPSED_FOLLOWUP_DAYS)

    (OUT_DIR / "retrieval_payload.json").write_text(
        json.dumps(payload, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )

    verify = build_retrieval_verification(cohort_df, payload, db_path_str, LAPSED_FOLLOWUP_DAYS)
    (OUT_DIR / "retrieval_verification.json").write_text(
        json.dumps(verify, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_retrieval_verification_md(verify)

    cohort_table = df_as_text(cohort_df)
    retrieval_json_str = json.dumps(payload, indent=2, default=str)
    rules_text = load_rules()

    gt = extract_hw2_ground_truth(cohort_df, payload, verify)

    qc_trials = max(1, int(qc_trials))
    qc_rows: list[dict] = []

    report_b_latest = ""
    report_a_latest = ""

    sys_r = _report_system_role()

    for t in range(qc_trials):
        trial_seed = None if qc_base_seed is None else int(qc_base_seed) + t

        user_a = _build_user_prompt(
            PROMPT_BASELINE_PATH,
            cohort_table=cohort_table,
            retrieval_json_str=retrieval_json_str,
            rules_text=rules_text,
            verify=verify,
            payload=payload,
            n_visits=n_visits,
            n_patients=n_patients,
        )
        t0 = time.time()
        report_a = agent_run(role=sys_r, task=user_a, model=MODEL, tools=None, seed=trial_seed)
        ta = time.time() - t0
        report_a_latest = report_a

        user_b = _build_user_prompt(
            PROMPT_GROUNDED_PATH,
            cohort_table=cohort_table,
            retrieval_json_str=retrieval_json_str,
            rules_text=rules_text,
            verify=verify,
            payload=payload,
            n_visits=n_visits,
            n_patients=n_patients,
        )
        t0 = time.time()
        report_b = agent_run(role=sys_r, task=user_b, model=MODEL, tools=None, seed=(trial_seed + 10_000) if trial_seed is not None else None)
        tb = time.time() - t0
        report_b_latest = report_b

        qc_rows.append(_score_report_row(trial_id=t, mode="baseline", report_text=report_a, gt=gt, runtime_s=ta))
        qc_rows.append(_score_report_row(trial_id=t, mode="grounded", report_text=report_b, gt=gt, runtime_s=tb))

    qc_results_df = pd.DataFrame(qc_rows)
    qc_results_df.to_csv(OUT_DIR / "qc_results.csv", index=False)

    summary = analyze_results(qc_results_df)
    write_summary_markdown(summary, qc_results_df, OUT_DIR / "qc_summary.md")

    (OUT_DIR / "prompt_a_baseline_report.md").write_text(report_a_latest, encoding="utf-8")
    (OUT_DIR / "prompt_b_grounded_report.md").write_text(report_b_latest, encoding="utf-8")
    (OUT_DIR / "homework2_comprehensive_report.md").write_text(report_b_latest, encoding="utf-8")

    return {
        "cohort_df": cohort_df,
        "report_full": report_b_latest,
        "report_baseline": report_a_latest,
        "retrieval_payload": payload,
        "verify_json": verify,
        "qc_summary": summary,
        "qc_results_df": qc_results_df,
        "n_visits": n_visits,
        "n_patients": n_patients,
    }


def run_live_homework2_pipeline(log=None):
    """
    App-safe live run:
    Agent 1 (OpenAI forced tool) → cohort → deterministic retrieval payload → grounded report.
    Does NOT run baseline-vs-grounded QC trials and does NOT write qc_results/qc_summary outputs.
    """
    del log

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    role1 = (
        "You are a clinical data assistant. You may ONLY satisfy requests by calling the "
        f"provided tool `{AGENT1_TOOL_NAME}`. Call it exactly once with arguments: {{}} "
        "(empty JSON object). Do not reply with prose, lists, or invented patients."
    )
    task1 = (
        "Pull every visit where PHQ-9 is above 15 and safety_concerns is Y. "
        f"Call `{AGENT1_TOOL_NAME}` with {{}}."
    )

    result1 = agent(
        messages=[{"role": "system", "content": role1}, {"role": "user", "content": task1}],
        model=MODEL,
        tools=[tool_list_phq9_safety],
        tool_choice=AGENT1_TOOL_CHOICE,
        all=True,
    )
    _write_agent1_tool_trace(MODEL, result1)
    cohort_df = _coerce_tool_result_to_dataframe(result1)
    if cohort_df is None:
        raise RuntimeError(
            "Agent 1 did not return cohort data via the tool. "
            "Confirm OPENAI_API_KEY in the repo-root `.env` (or exported), use a tools-capable model (e.g. gpt-4o-mini / gpt-4o), and retry."
        )

    n_visits = int(len(cohort_df))
    n_patients = (
        int(cohort_df["patient_id"].nunique()) if "patient_id" in cohort_df.columns else n_visits
    )

    patient_ids = [int(x) for x in cohort_df["patient_id"].dropna().unique()] if n_visits else []
    db_path_str = str(DB_PATH.resolve())
    payload = build_cohort_retrieval_payload(db_path_str, patient_ids, lapsed_min_days=LAPSED_FOLLOWUP_DAYS)
    (OUT_DIR / "retrieval_payload.json").write_text(
        json.dumps(payload, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )

    verify = build_retrieval_verification(cohort_df, payload, db_path_str, LAPSED_FOLLOWUP_DAYS)
    (OUT_DIR / "retrieval_verification.json").write_text(
        json.dumps(verify, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_retrieval_verification_md(verify)

    cohort_table = df_as_text(cohort_df)
    retrieval_json_str = json.dumps(payload, indent=2, default=str)
    rules_text = load_rules()
    sys_r = _report_system_role()

    user_b = _build_user_prompt(
        PROMPT_GROUNDED_PATH,
        cohort_table=cohort_table,
        retrieval_json_str=retrieval_json_str,
        rules_text=rules_text,
        verify=verify,
        payload=payload,
        n_visits=n_visits,
        n_patients=n_patients,
    )
    report_b = agent_run(role=sys_r, task=user_b, model=MODEL, tools=None, seed=42)

    (OUT_DIR / "prompt_b_grounded_report.md").write_text(report_b, encoding="utf-8")
    (OUT_DIR / "homework2_comprehensive_report.md").write_text(report_b, encoding="utf-8")

    return {
        "cohort_df": cohort_df,
        "report_full": report_b,
        "retrieval_payload": payload,
        "verify_json": verify,
        "n_visits": n_visits,
        "n_patients": n_patients,
    }


if __name__ == "__main__":
    print("Homework 2 pipeline (clinical_pipeline.py)…", flush=True)
    print(f"OpenAI model: {MODEL}", flush=True)
    print(f"Database: {DB_PATH}", flush=True)
    if not DB_PATH.is_file():
        raise SystemExit(f"Missing database: {DB_PATH}")
    ntrials = int(os.environ.get("HW2_QC_TRIALS", "1"))
    out = run_full_homework2_pipeline(qc_trials=ntrials)
    print(f"Done. Visits: {out['n_visits']}, patients: {out['n_patients']}. Outputs in {OUT_DIR}/", flush=True)
