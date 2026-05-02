# QC Validation Summary

## 1. Absolute validity

**Overall (all outputs):**
- Report quality score (mean): 71.95
- 95% CI: [42.84, 101.07]
- Pass rate: 50.0%

**By prompt mode:**

**Prompt A / Baseline:**
- Report quality score (mean): 50.95
- Pass rate: 0.0% [95% CI: 0.0–79.3]
- Structural error rate: 0.0%

**Prompt B / Grounded Executive Report:**
- Report quality score (mean): 92.96
- Pass rate: 100.0% [95% CI: 20.7–100.0]
- Structural error rate: 0.0%

Prompt B / Grounded Executive Report is shown as the primary report in the app; compare against Prompt A / Baseline for QC.

Prompt B / Grounded Executive Report achieved higher validity and pass rates than Prompt A / Baseline in this run.

- Strict pass criteria emphasize sections, cohort numeric fidelity (visits/patients/lapsed follow-up), provider count alignment, and hallucination proxies (extra identifiers/numbers).
- Signals are heuristic on synthetic/educational data — not diagnostic or regulatory validation.

## Pass-rate comparison

- Prompt A / Baseline pass rate: 0.0% [95% CI: 0.0–79.3]
- Prompt B / Grounded Executive Report pass rate: 100.0% [95% CI: 20.7–100.0]
- Difference (Prompt B − Prompt A): 100.0 pp [95% bootstrap CI: 100.0–100.0]
- McNemar (exact binomial): p = 1 (discordant pairs: baseline pass / grounded fail = 0, baseline fail / grounded pass = 1).

Pass rate difference was not statistically significant under the paired McNemar test.


## 2. Prompt A / Baseline vs Prompt B / Grounded Executive Report
- Prompt A / Baseline mean validity score: 50.95
- Prompt B / Grounded Executive Report mean validity score: 92.96
- Paired t-statistic: None
- Effect size (Cohen's d): 0.00

## 3. Confidence validation
Confidence-validation-by-label plots are retained for QC framework compatibility but are not primary for HW2 (dominant confidence is often neutral).

Summary by dominant confidence label (structured payload):
- Neutral: report quality score 71.95, numeric alignment 84.2%, pass rate 50.0%

Confidence score correlation was not computed (missing or insufficient variation in confidence scores).

## 4. Failure Mode Analysis

Common QC signals reflect section coverage, cohort numeric fidelity, and hallucination heuristics.

| Failure mode | Count | Share |
|---|---:|---:|
| Did not pass absolute validity | 1 | 50.0% |
| Visit count fidelity below 1.0 | 1 | 50.0% |
| Missing required sections | 1 | 50.0% |
| Unsupported patient identifier citation(s) | 0 | 0.0% |
| Unsupported provider name signal(s) | 0 | 0.0% |
| Hallucinated state mention(s) | 0 | 0.0% |
| Clinically unsupported numeral occurrence(s) | 0 | 0.0% |
| Patient count fidelity below 1.0 | 0 | 0.0% |
| Lapsed follow-up count fidelity below 1.0 | 0 | 0.0% |
| Numeric accuracy below 0.60 | 0 | 0.0% |
| Confidence label match below 1.0 | 0 | 0.0% |
| Low/moderate confidence disclosure below 1.0 (where applicable) | 0 | 0.0% |
| Unsupported structured confidence claim(s) | 0 | 0.0% |
| Grader: unsupported_claims non-empty | 0 | 0.0% |
| Grader: confidence_misuse non-empty | 0 | 0.0% |

Review row-level CSV columns for detailed validity metrics.

The most common failure signals were: Did not pass absolute validity (50.0% of rows); Visit count fidelity below 1.0 (50.0% of rows); Missing required sections (50.0% of rows). Counts can overlap (one row may trigger multiple flags). Use the table above and per-row metrics in `qc_results.csv` to prioritize fixes.

## 5. Row-level review
- Inspect `qc_results.csv`: Potential Unsupported Statements, Confidence Wording Issues, grader payload, and report text (column names remain machine-readable).

## 6. Example outputs

```text
    mode  trial_id  validity_score_0_100  numeric_accuracy_score  visit_count_match  patient_count_match  clinically_unsupported_number_count  unsupported_patient_identifier_count
baseline         0                 50.95                0.756000               0.55                  1.0                                  0.0                                   0.0
grounded         0                 92.96                0.928571               1.00                  1.0                                  0.0                                   0.0
```