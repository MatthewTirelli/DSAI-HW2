# clinical_pipeline.py
# Homework 2 — tool cohort extraction, RAG retrieval payload, two-agent LLM flow
# Shiny UI (app/app.py) calls run_full_homework2_pipeline(); optional CLI via __main__

# 0. SETUP ###################################

## 0.1 Load Packages ############################

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

## 0.2 Local imports ############################

HW2_ROOT = Path(__file__).resolve().parent
if str(HW2_ROOT) not in sys.path:
    sys.path.insert(0, str(HW2_ROOT))

from functions import DEFAULT_MODEL, OLLAMA_HOST, agent, agent_run, df_as_text  # noqa: E402
from retrieval import build_cohort_retrieval_payload  # noqa: E402

## 0.3 Configuration ############################

MODEL = DEFAULT_MODEL
PHQ9_MIN_EXCLUSIVE = 15
LAPSED_FOLLOWUP_DAYS = 30

_env_db = os.environ.get("PATIENTS_DB")
DB_PATH = Path(_env_db).expanduser().resolve() if _env_db else HW2_ROOT / "patients.db"
OUT_DIR = HW2_ROOT / "out"
RULES_PATH = HW2_ROOT / "clinical_rag_rules.yaml"

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

# 1. TOOL + HELPERS ###################################


def list_phq9_elevated_with_safety_concerns(**_kwargs):
    """
    Visits where PHQ-9 > 15 and safety_concerns is Y.
    Ignores model-supplied paths so only PATIENTS_DB / HW2/patients.db is read.
    """
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
    """Flatten clinical_rag_rules.yaml into model-readable text."""
    raw = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    return yaml.dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _coerce_tool_result_to_dataframe(result):
    """Normalize agent() return value to cohort DataFrame."""
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
    """Structured checks: cohort IDs, provider sums, lapsed row counts, SQL visit total."""
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


def run_full_homework2_pipeline(log=None):
    """
    Agent 1 (forced tool) → cohort DataFrame → RAG JSON → verification → Agent 2 report.
    Writes artifacts under out/. Returns dict for the Shiny server.
    """
    del log  # reserved for future UI logging hooks

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
            "Use a tool-capable model (e.g. llama3.2), upgrade Ollama, and retry."
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

    role2 = (
        "You are a clinical chart reviewer writing an administrative and clinical synthesis for a quality committee.\n"
        "Use ONLY facts present in the user message (cohort table and analytics JSON).\n\n"
        + load_rules()
    )
    task2 = (
        "Ground-truth materials for this report:\n\n"
        f"1) Cohort rule: PHQ-9 > 15 and safety_concerns = Y.\n"
        f"2) Qualifying visits (table rows): {n_visits}\n"
        f"3) Unique patients (patient_id in table): {n_patients}\n\n"
        "Full cohort Markdown table:\n"
        f"{cohort_table}\n\n"
        "Retrieval analytics JSON (deterministic; cite these counts exactly):\n"
        f"```json\n{retrieval_json_str}\n```\n\n"
        "Write a comprehensive Markdown report with sections:\n"
        "## Executive summary\n"
        "## Cohort overview\n"
        "## Provider and access patterns\n"
        "## Medication and documentation themes\n"
        "## Lapsed follow-up and care continuity\n"
        "## Data limitations and audit notes\n"
        "Do not invent patient names, IDs, dates, or counts not supported by the table or JSON."
    )

    report_md = agent_run(role=role2, task=task2, model=MODEL, tools=None, output="text")
    (OUT_DIR / "homework2_comprehensive_report.md").write_text(report_md, encoding="utf-8")

    return {
        "cohort_df": cohort_df,
        "report_full": report_md,
        "verify_json": verify,
        "n_visits": n_visits,
        "n_patients": n_patients,
    }


# 2. CLI ###################################
if __name__ == "__main__":
    print("Homework 2 pipeline (clinical_pipeline.py)…", flush=True)
    print(f"Ollama: {OLLAMA_HOST} — model: {MODEL}", flush=True)
    print(f"Database: {DB_PATH}", flush=True)
    if not DB_PATH.is_file():
        raise SystemExit(f"Missing database: {DB_PATH}")
    out = run_full_homework2_pipeline()
    print(f"Done. Visits: {out['n_visits']}, patients: {out['n_patients']}. Outputs in {OUT_DIR}/", flush=True)
