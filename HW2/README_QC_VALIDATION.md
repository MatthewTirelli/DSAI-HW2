# QC and Validation Framework

## 1) App overview

This app identifies synthetic high-risk behavioral health visits and generates a grounded clinical summary for dashboard review.

- **High-risk definition:** `PHQ-9 > 15` and `safety_concerns = Y`
- **Primary output:** grounded executive summary based on the high-risk cohort and deterministic retrieval payload
- **Data status:** synthetic and educational only; not for clinical decision-making or regulatory validation

---

## 2) Report generation pipeline

The report and QC flow is:

1. **Source data** is loaded from `patients.db`.
2. **Agent/tool layer** identifies high-risk visits matching the cohort rule.
3. **Deterministic retrieval layer** builds structured grounding payload from SQL-derived cohort context.
4. **Report writer** generates the grounded executive summary.
5. **Validator** grades the generated report against source-derived ground truth.
6. **Dashboard** shows:
   - current-report quality checks for the currently displayed report
   - batch QC comparison summaries from saved experiment outputs

---

## 3) Ground truth / retrieval payload

Validation is anchored to a deterministic retrieval payload, which functions as the source of truth for grading.

Typical fields include:

- total high-risk visits
- unique patient count
- lapsed follow-up count
- provider visit counts
- medication mention summaries/themes
- retrieval/data consistency checks (verification outputs)

This design keeps grading tied to deterministic data artifacts rather than model free text.

---

## 4) Validation rubric

Automated grading checks include:

- required section headings
- visit count fidelity
- patient count fidelity
- lapsed follow-up count fidelity
- provider name support
- patient identifier support
- clinically unsupported number detection
- weighted overall quality score (`0–100`)
- strict pass/fail determination

### Pass logic (strict)

A report passes only when structural and numeric checks are satisfied and unsupported identifiers/unsupported claims are absent under the configured rubric.

---

## 5) Current report QC

The dashboard performs **live validation** for the currently displayed grounded report.

- This grades only the report currently shown in the app.
- It does **not** rerun the 50-trial baseline-vs-grounded experiment.
- Current-report QC output should remain in memory (or separate output), and should not overwrite batch QC artifacts.

---

## 6) Batch QC experiment

The batch QC experiment is a paired evaluation over repeated trials (target: 50):

- same cohort/retrieval payload for paired comparisons
- **Prompt A / Baseline** report generated
- **Prompt B / Grounded Executive Report** generated
- both graded by the same deterministic validator
- row-level outputs written to `out/qc_results.csv`

For 50 paired trials, expected row count is:

- `50` baseline rows + `50` grounded rows = `100` rows total

---

## 7) Dashboard batch QC behavior

Expected batch comparison behavior:

- read saved batch results from `out/qc_results.csv` (or designated saved batch source)
- do **not** rerun the full 50-trial experiment during normal app use
- expose diagnostics such as:
  - batch CSV path
  - row count
  - paired runs inferred
  - whether batch data was regenerated during app run
- if paired runs are fewer than 50, show a warning and do not label as a 50-run batch

---

## 8) Statistical outputs (plain language)

| Metric | What it means |
|---|---|
| Mean quality score by prompt mode | Average rubric score (`0–100`) for baseline vs grounded outputs |
| Pass rate by prompt mode | Share of reports meeting strict pass criteria |
| Pass-rate confidence intervals | Uncertainty range around each pass-rate estimate |
| Pass-rate difference | Grounded pass rate minus baseline pass rate |
| Bootstrap CI for pass-rate difference | Resampled uncertainty interval for pass-rate improvement |
| Paired t-test (score difference) | Tests whether grounded scores exceed baseline scores across paired trials |
| Cohen's d | Standardized effect size of score differences |
| McNemar exact test | Paired pass/fail test for conversion asymmetry (fail→pass vs pass→fail) |
| Failure mode counts/shares | Most frequent rubric failure signals across rows |

### Interpretation guidance

- **Paired t-test:** evaluates whether grounded summaries score higher than baseline summaries.
- **Cohen's d:** can be very large when paired differences are consistently positive with low variance.
- **McNemar's test:** checks whether grounded summaries convert failures to passes more often than the reverse.
- **Bootstrap CI of `[100, 100]` for pass-rate difference:** occurs when all baseline fail and all grounded pass across paired runs.

---

## 9) Current 50-run QC results

### Overall

- Mean report quality score: **71.64**
- 95% CI: **[67.70, 75.58]**
- Overall pass rate: **50.0%**

### Prompt A / Baseline

- Mean quality score: **51.64**
- Pass rate: **0.0%** [95% CI: **0.0–7.1**]
- Structural error rate: **0.0%**

### Prompt B / Grounded Executive Report

- Mean quality score: **91.64**
- Pass rate: **100.0%** [95% CI: **92.9–100.0**]
- Structural error rate: **0.0%**

### Pass-rate comparison

- Difference: **+100.0 percentage points**
- Bootstrap 95% CI: **[100.0, 100.0]**
- McNemar exact p-value: **1.776e-15**
- Discordant pairs:
  - baseline pass / grounded fail = **0**
  - baseline fail / grounded pass = **50**

### Score comparison

- Paired t-statistic: **95.37**
- Cohen's d: **19.70**

### Failure mode summary

- Did not pass absolute validity: **50 rows / 50.0%**
- Visit count fidelity below 1.0: **50 rows / 50.0%**
- Missing required sections: **50 rows / 50.0%**
- Unsupported patient identifier citations: **0**
- Unsupported provider name signals: **0**
- Clinically unsupported numeral occurrences: **0**
- Patient count fidelity below 1.0: **0**
- Lapsed follow-up count fidelity below 1.0: **0**

---

## 10) Interpretation of results

Grounded prompting substantially improved report quality in this synthetic paired experiment. Prompt B achieved perfect pass performance under the automated rubric, while Prompt A failed systematically due to missing required sections and visit-count mismatch. The pass-rate improvement is statistically significant under McNemar's test. The very large Cohen's d reflects consistent, low-variance paired differences, not a literal claim that the model is "19.7 times better." These findings support the grounding approach for this educational app but are not clinical validation.

---

## 11) Files and commands

### File/component chart (how each file contributes to grading/validation)

| File | Role in framework | How it is incorporated |
|---|---|---|
| `patients.db` | Source dataset | Provides visit/patient records used to construct high-risk cohort |
| `clinical_pipeline.py` | Orchestration pipeline | Runs cohort extraction, retrieval payload generation, report generation, and QC flow |
| `retrieval.py` | Deterministic retrieval builder | Produces grounding payload used as validator source of truth |
| `qc/validators.py` | Validation rule engine | Computes section/numeric/support checks and mismatch signals |
| `qc/scoring.py` | Score rollup logic | Converts validator metrics into weighted `0–100` score and pass/fail |
| `qc/statistical_analysis.py` | Batch statistics | Computes by-mode summaries, paired tests, CIs, McNemar, failure mode aggregates |
| `qc/report_generation.py` | Batch summary writer | Generates markdown QC narrative/tables from statistics |
| `qc/run_hw2_qc_experiment.py` | Batch runner | Executes paired baseline/grounded trials and writes batch outputs |
| `qc/prompts/hw2_baseline_prompt.txt` | Baseline prompt template | Defines Prompt A behavior for paired experiment |
| `qc/prompts/hw2_grounded_prompt.txt` | Grounded prompt template | Defines Prompt B grounded report format/content |
| `out/qc_results.csv` | Row-level batch artifact | Stores one graded row per mode per trial |
| `out/qc_summary.md` | Batch narrative artifact | Stores summarized statistics and interpretation |
| `app/app.py` | Dashboard UI logic | Displays current-report checks and batch QC summaries |

### Commands

```bash
python qc/run_hw2_qc_experiment.py --n-trials 50
```

```bash
caffeinate -dimsu python qc/run_hw2_qc_experiment.py --n-trials 50
```

---

## 12) Developer notes / gotchas

- Do not let normal dashboard runs overwrite `out/qc_results.csv`.
- Keep current report QC and batch QC as separate concerns.
- Do not label a saved batch as "50-run" unless `paired_runs >= 50`.
- If `qc_results.csv` has only 2 rows, that is 1 paired run (not the full 50-run experiment).
- Future enhancement: add bootstrap CI for **mean score difference** (in addition to pass-rate difference).

