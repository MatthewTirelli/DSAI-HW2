#!/usr/bin/env python3
"""
Re-run HW2 validators + scoring on existing `qc_results.csv` rows (same `report_text`).

Uses `out/retrieval_payload.json` + `out/retrieval_verification.json` and a fresh SQL cohort
pull — **no OpenAI**. Refreshes `qc_summary.md`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import clinical_pipeline as cp  # noqa: E402
from qc.qc_regrade_bundle import regraded_dataframe, write_regraded_artifacts  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Regrade saved HW2 QC rows (fast, no LLM).")
    parser.add_argument("--qc-csv", type=Path, default=cp.OUT_DIR / "qc_results.csv", help="Input CSV with report_text")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=cp.OUT_DIR,
        help="Must contain retrieval_payload.json + retrieval_verification.json; writes qc_results + summary here",
    )
    args = parser.parse_args()

    if not args.qc_csv.is_file():
        raise SystemExit(f"Missing {args.qc_csv}")

    cohort_df, payload, verify = cp.load_qc_reference_bundle(args.out_dir)
    out_df = regraded_dataframe(
        qc_csv=args.qc_csv,
        cohort_df=cohort_df,
        retrieval_payload=payload,
        verification_json=verify,
    )
    write_regraded_artifacts(
        out_df,
        out_dir=args.out_dir,
        csv_name="qc_results.csv",
        md_name="qc_summary.md",
    )
    print(f"Regraded {len(out_df)} rows → {args.out_dir / 'qc_results.csv'}", flush=True)


if __name__ == "__main__":
    main()
