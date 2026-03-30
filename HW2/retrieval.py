# retrieval.py
# Homework 2 — Cohort-scoped SQLite retrieval and aggregates (RAG payload builders)
# All logic lives in HW2/; truth comes from SQL/pandas, not the LLM.

# 0. SETUP ###################################

## 0.1 Load Packages ############################

import re
import sqlite3
from collections import Counter
from typing import Any

import pandas as pd  # for data manipulation

# 1. COHORT SQL HELPERS ###################################


def cohort_provider_concentration(conn: sqlite3.Connection, patient_ids: list[int]) -> dict[str, Any]:
    """
    Visit counts and distinct-patient counts per provider, limited to cohort patients.
    """
    ids = sorted({int(x) for x in patient_ids})
    if not ids:
        return {
            "visit_counts_by_provider": [],
            "cohort_patient_count": 0,
            "note": "Empty cohort — no patient_ids.",
        }

    placeholders = ",".join("?" * len(ids))
    sql = f"""
    SELECT
      provider,
      COUNT(*) AS visit_count,
      COUNT(DISTINCT patient_id) AS distinct_patient_count
    FROM visits
    WHERE patient_id IN ({placeholders})
    GROUP BY provider
    ORDER BY visit_count DESC, provider ASC;
    """
    df = pd.read_sql_query(sql, conn, params=ids)
    return {
        "visit_counts_by_provider": df.to_dict(orient="records"),
        "cohort_patient_count": len(ids),
        "note": "Counts include all visits for cohort members (not only PHQ-9/safety-flag visits).",
    }


def _tokenize_medications(text: str) -> list[str]:
    """Split free-text medication strings into coarse tokens for frequency summary."""
    if not isinstance(text, str) or not text.strip():
        return []
    parts = re.split(r"[,;|/\n]+", text)
    out = []
    for p in parts:
        t = p.strip()
        if len(t) < 2:
            continue
        out.append(t[:120])
    return out


def cohort_medication_summary(conn: sqlite3.Connection, patient_ids: list[int], top_n: int = 25) -> dict[str, Any]:
    """
    Frequency summary of medication substrings recorded on cohort visits.
    Parsing is heuristic (comma/semicolon split); use for administrative themes only.
    """
    ids = sorted({int(x) for x in patient_ids})
    if not ids:
        return {"top_medication_strings": [], "note": "Empty cohort."}

    placeholders = ",".join("?" * len(ids))
    sql = f"""
    SELECT medications
    FROM visits
    WHERE patient_id IN ({placeholders})
      AND medications IS NOT NULL
      AND TRIM(medications) != '';
    """
    df = pd.read_sql_query(sql, conn, params=ids)
    counter: Counter[str] = Counter()
    for raw in df["medications"].astype(str):
        for tok in _tokenize_medications(raw):
            counter[tok] += 1

    top = counter.most_common(top_n)
    return {
        "top_medication_strings": [{"text": k, "visit_row_mentions": v} for k, v in top],
        "unique_token_count": len(counter),
        "note": "Counts are mentions after naive splitting, not validated prescriptions.",
    }


def retrieve_cohort_not_seen_days(
    conn: sqlite3.Connection, patient_ids: list[int], min_days: int = 30
) -> pd.DataFrame:
    """
    Cohort patients whose *last* documented visit is more than min_days ago.
    Same pattern as course retrieve_not_seen_days, plus patient_id IN cohort.
    """
    ids = sorted({int(x) for x in patient_ids})
    if not ids:
        return pd.DataFrame()

    placeholders = ",".join("?" * len(ids))
    sql = f"""
    SELECT
      p.id AS patient_id,
      p.name AS patient_name,
      v.visit_date AS last_visit_date,
      v.diagnosis AS last_visit_diagnosis,
      v.provider AS last_visit_provider,
      CAST(julianday('now') - julianday(v.visit_date) AS INTEGER) AS days_since_last_visit
    FROM patients p
    JOIN visits v ON v.patient_id = p.id
    WHERE p.id IN ({placeholders})
      AND v.id = (
        SELECT v2.id FROM visits v2
        WHERE v2.patient_id = p.id
        ORDER BY v2.visit_date DESC, v2.id DESC
        LIMIT 1
      )
      AND CAST(julianday('now') - julianday(v.visit_date) AS INTEGER) > ?
    ORDER BY last_visit_date ASC;
    """
    params = list(ids) + [min_days]
    return pd.read_sql_query(sql, conn, params=params)


def summarize_lapsed_cohort(df: pd.DataFrame) -> dict[str, Any]:
    """Aggregate stats for overdue follow-up rows (Python/SQL truth for the LLM)."""
    if df.empty:
        return {
            "patient_count": 0,
            "mean_days_since_last_visit": None,
            "median_days_since_last_visit": None,
            "diagnosis_counts_at_last_visit": {},
            "last_visit_provider_counts": {},
            "all_lapsed_patients_share_same_last_provider": False,
            "single_last_provider_name_if_applicable": None,
            "note": "No cohort patients exceed the days-since-last-visit threshold.",
        }

    mean_d = float(df["days_since_last_visit"].mean())
    med_d = float(df["days_since_last_visit"].median())
    diag_counts = {str(k): int(v) for k, v in df["last_visit_diagnosis"].value_counts().items()}
    prov_counts = {str(k): int(v) for k, v in df["last_visit_provider"].value_counts().items()}
    n_unique = df["last_visit_provider"].nunique()
    single_provider = n_unique == 1
    single_name = str(df["last_visit_provider"].iloc[0]) if single_provider else None

    return {
        "patient_count": int(len(df)),
        "mean_days_since_last_visit": round(mean_d, 1),
        "median_days_since_last_visit": round(med_d, 1),
        "diagnosis_counts_at_last_visit": diag_counts,
        "last_visit_provider_counts": prov_counts,
        "all_lapsed_patients_share_same_last_provider": bool(single_provider),
        "single_last_provider_name_if_applicable": single_name,
        "note": (
            "Days since last visit measure documentation gaps in this synthetic DB, "
            "not confirmed missed appointments."
        ),
    }


def build_cohort_retrieval_payload(
    db_path: str,
    patient_ids: list[int],
    lapsed_min_days: int = 30,
) -> dict[str, Any]:
    """
    One JSON-serializable dict: provider concentration, medication summary, lapsed follow-up.
    """
    with sqlite3.connect(db_path) as conn:
        prov = cohort_provider_concentration(conn, patient_ids)
        meds = cohort_medication_summary(conn, patient_ids)
        lapsed_df = retrieve_cohort_not_seen_days(conn, patient_ids, min_days=lapsed_min_days)
        lapsed_summary = summarize_lapsed_cohort(lapsed_df)

    return {
        "cohort_patient_ids": sorted({int(x) for x in patient_ids}),
        "lapsed_followup_threshold_days": lapsed_min_days,
        "provider_concentration": prov,
        "medications_summary": meds,
        "lapsed_followup": {
            "retrieval_rows": lapsed_df.to_dict(orient="records"),
            "row_count": int(len(lapsed_df)),
            "summary_statistics": lapsed_summary,
        },
    }
