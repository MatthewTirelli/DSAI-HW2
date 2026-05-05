#!/usr/bin/env python3
"""
Run repeated Prompt A/B generations on the same deterministic cohort + retrieval payload,
then refresh QC summaries under HW2/out/.

Requires OPENAI_API_KEY (set in `.env` at repo root or `HW2/.env`; see README). Optional: OPENAI_MODEL, HW2_QC_TRIALS, PATIENTS_DB.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import clinical_pipeline as cp  # noqa: E402
from qc.qc_regrade_bundle import regraded_dataframe, write_regraded_artifacts  # noqa: E402


def _archive_batch_outputs(out_dir: Path, n_trials: int) -> tuple[Path, Path]:
    batches_dir = out_dir / "qc_batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"qc_{n_trials}trials_{stamp}"

    src_csv = out_dir / "qc_results.csv"
    src_md = out_dir / "qc_summary.md"
    dst_csv = batches_dir / f"{stem}.csv"
    dst_md = batches_dir / f"{stem}.md"
    if dst_csv.exists() or dst_md.exists():
        raise RuntimeError(f"Archive targets already exist: {dst_csv} / {dst_md}")
    shutil.copy2(src_csv, dst_csv)
    shutil.copy2(src_md, dst_md)
    (batches_dir / "LATEST_IMMUTABLE_CSV.txt").write_text(str(dst_csv), encoding="utf-8")
    (batches_dir / "LATEST_IMMUTABLE_SUMMARY.txt").write_text(str(dst_md), encoding="utf-8")
    return dst_csv, dst_md


def main() -> None:
    parser = argparse.ArgumentParser(description="HW2 paired QC experiment (Prompt A vs Prompt B)")
    parser.add_argument(
        "--regrade-existing",
        action="store_true",
        help="Re-run validators + scoring + stats on existing out/qc_results.csv (updates qc_results.csv + qc_summary.md in place; no OpenAI).",
    )
    parser.add_argument("--n-trials", type=int, default=int(os.environ.get("HW2_QC_TRIALS", "5")))
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional base seed forwarded to OpenAI report calls (paired trials increment). Default: 42 in pipeline.",
    )
    args = parser.parse_args()

    if not cp.DB_PATH.is_file():
        raise SystemExit(f"Missing database at {cp.DB_PATH}")

    if args.regrade_existing:
        print("Regrading existing reports only — no OpenAI calls", flush=True)
        src = cp.OUT_DIR / "qc_results.csv"
        if not src.is_file():
            raise SystemExit(f"Missing {src}; run a full experiment first.")
        cohort_df, payload, verify = cp.load_qc_reference_bundle(cp.OUT_DIR)
        df = regraded_dataframe(
            qc_csv=src,
            cohort_df=cohort_df,
            retrieval_payload=payload,
            verification_json=verify,
        )
        write_regraded_artifacts(
            df,
            out_dir=cp.OUT_DIR,
            csv_name="qc_results.csv",
            md_name="qc_summary.md",
        )
        print(f"Regraded QC rows: {len(df)} → {cp.OUT_DIR / 'qc_results.csv'}", flush=True)
        print(f"Summary markdown: {cp.OUT_DIR / 'qc_summary.md'}", flush=True)
        return

    out = cp.run_full_homework2_pipeline(qc_trials=max(1, args.n_trials), qc_base_seed=args.seed)

    csv_path = cp.OUT_DIR / "qc_results.csv"
    md_path = cp.OUT_DIR / "qc_summary.md"
    print(f"QC rows: {len(out['qc_results_df'])} written to {csv_path}", flush=True)
    print(f"Summary markdown: {md_path}", flush=True)
    archived_csv, archived_md = _archive_batch_outputs(cp.OUT_DIR, max(1, args.n_trials))
    print(f"Archived immutable CSV: {archived_csv}", flush=True)
    print(f"Archived immutable summary: {archived_md}", flush=True)


if __name__ == "__main__":
    main()
