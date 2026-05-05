# AI qualitative review — statistical summary

**This AI qualitative review was conducted offline on already-generated reports and does not modify the app, deterministic validator, or report generation pipeline.**

## 1) Overview

- **Input CSV:** `out/qc_batches/qc_50trials_20260505T130841Z.csv`
- **Output CSV:** `out/ai_qualitative_review_results.csv`
- **Model:** `gpt-4o-mini`
- **Reports with successful AI review:** 100
- **Baseline rows reviewed:** 50
- **Grounded rows reviewed:** 50
- **Paired trials (both modes reviewed):** 50

## 2) Mean AI qualitative scores by mode

| Dimension | Baseline mean | Grounded mean | Difference (grounded − baseline) |
|---|---:|---:|---:|
| clarity | 4.860 | 3.080 | -1.780 |
| clinical_usefulness | 4.460 | 3.000 | -1.460 |
| coherence | 4.940 | 3.380 | -1.560 |
| completeness | 4.460 | 3.160 | -1.300 |
| overall_quality | 4.460 | 3.040 | -1.420 |

## 3) Paired statistical comparisons (by trial_id)

Paired difference = grounded − baseline for each dimension.

| Dimension | n paired | Baseline mean | Grounded mean | Mean diff | Paired t | p-value | Cohen's d (paired) | Bootstrap 95% CI (mean diff) |
|---|---:|---:|---:|---:|---:|---:|---|---|
| clarity | 50 | 4.860 | 3.080 | -1.780 | -30.0787 | 3.017e-33 | -4.2970 | [-1.8800, -1.6600] |
| clinical_usefulness | 50 | 4.460 | 3.000 | -1.460 | -20.5057 | 1.078e-25 | -2.9294 | [-1.6000, -1.3200] |
| coherence | 50 | 4.940 | 3.380 | -1.560 | -18.0403 | 2.788e-23 | -2.5772 | [-1.7200, -1.3800] |
| completeness | 50 | 4.460 | 3.160 | -1.300 | -14.2118 | 5.231e-19 | -2.0303 | [-1.4800, -1.1200] |
| overall_quality | 50 | 4.460 | 3.040 | -1.420 | -18.6652 | 6.471e-24 | -2.6665 | [-1.5600, -1.2800] |

## 4) Relationship to deterministic QC

- Pearson correlation (**ai_overall_quality** vs **validity_score_0_100**): **-0.888**

| Deterministic pass | Mean ai_overall_quality | n |
|---|:---:|---:|
| passed | 3.040 | 50 |
| failed | 4.460 | 50 |

Deterministic validation measures factual/structural grounding against the retrieval payload; AI qualitative review measures perceived narrative clarity and usefulness **only**. These are complementary, not interchangeable.

## 5) Qualitative themes

### Baseline — strengths (exact string counts)

- `Well-structured sections that clearly outline key findings.` — 31
- `Clear identification of high-risk patient characteristics and provider involvement.` — 5
- `Logical flow of information from overall patterns to specific concerns.` — 5
- `Logical flow from overall patterns to specific concerns.` — 5
- `Well-structured narrative with clear sections` — 4
- `Actionable insights regarding provider involvement and follow-up care.` — 3
- `Actionable insights regarding provider involvement and medication management.` — 3
- `Well-structured sections that clearly outline key findings and concerns.` — 3
- `The report is well-structured and clearly presents the findings in a logical manner.` — 2
- `Well-structured and clear presentation of findings.` — 2

### Baseline — weaknesses (exact string counts)

- `None identified.` — 14
- `Could provide more specific actionable insights for care teams.` — 6
- `Some areas could benefit from more specific actionable insights.` — 3
- `Minor awkward phrasing in certain sections.` — 3
- `Could provide more actionable insights or recommendations for care teams.` — 3
- `There are no significant weaknesses noted in the narrative.` — 2
- `Some areas could benefit from deeper analysis.` — 2
- `Some areas, like medication management, could benefit from deeper analysis.` — 2
- `Could provide more depth in discussing medication adherence.` — 2
- `Some sections could benefit from more detailed explanations or examples to enhance understanding.` — 1

### Grounded — strengths (exact string counts)

- `Provides a structured overview of the cohort and key metrics.` — 9
- `Provides a structured overview of the cohort and relevant patterns.` — 5
- `The report provides a structured overview of the cohort, including visit counts and medication themes.` — 5
- `Includes detailed information on provider access and medication themes.` — 3
- `Includes detailed information on provider visits and medication mentions.` — 2
- `The report provides a structured overview of the cohort and includes relevant sections such as provider patterns and medication themes.` — 2
- `Highlights lapsed follow-up cases which are critical for care continuity.` — 2
- `The report provides a structured overview of the cohort, including visit counts and medication mentions.` — 2
- `It highlights lapsed follow-up, which is crucial for care continuity.` — 2
- `The report provides a structured overview of the cohort and relevant patterns in provider access and medication usage.` — 2

### Grounded — weaknesses (exact string counts)

- `Some sections could benefit from clearer connections between data points.` — 4
- `Some sections are overly detailed with lists that may overwhelm the reader.` — 3
- `Lacks depth in analysis of trends and implications.` — 3
- `The narrative lacks depth in analysis and interpretation of the data, making it less actionable for clinical teams.` — 3
- `Lacks deeper analysis or interpretation of the data presented.` — 2
- `The narrative could benefit from clearer connections between sections and more in-depth analysis of the data presented.` — 2
- `Lacks depth in analysis of provider and access patterns.` — 2
- `The narrative lacks depth in discussing the implications of the data and does not provide actionable insights for care teams.` — 2
- `The narrative lacks depth in discussing the implications of the data presented.` — 2
- `The narrative lacks depth in discussing the implications of the findings and does not provide actionable insights for care teams.` — 2

## 6) Interpretation

**Sign convention:** positive differences favor **grounded** narrative scores; negative differences favor **baseline** narrative scores.

The overall quality mean difference was **-1.420** points on a 1–5 scale (grounded minus baseline mean scores), meaning **grounded reports scored lower** than baseline reports on perceived overall narrative quality across this sample.

Across **all five** AI qualitative dimensions, **mean differences were strictly negative**, so **baseline** reports scored higher than **grounded** on average.

Using the available deterministic columns in this file, **grounded reports performed better under deterministic QC** (for example higher mean validity scores or higher pass-through on strict deterministic checks).

Those deterministic findings therefore **qualitatively diverge from the narrative rubric summarized above**: scores can disagree because rubrics measure **different constructs**.
**This suggests a tradeoff between stricter grounding/numeric fidelity and perceived narrative readability, clarity, or usefulness** — deterministic structure can impose constraints that make prose feel tighter or less free-flowing to reviewers even when grounding improves validity checks.

Deterministic QC evaluates **factual/structural grounding** (for example fidelity to cohort counts and required sections). AI qualitative review evaluates **narrative quality only**. These lenses are complementary and **not interchangeable**.

**Caution:** AI qualitative review is supplemental and inherently subjective — it must not substitute for deterministic validation.

**Scope:** Synthetic educational data limits external generalization.

## 7) Output files

- `out/ai_qualitative_review_results.csv` — row-level AI qualitative scores and JSON-coded themes
- `out/ai_qualitative_review_summary.md` — this summary document
