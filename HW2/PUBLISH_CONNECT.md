# Publishing the Shiny app (Posit / Systems Connect)

Publishing overview for your organization’s Connect server:

[Publishing overview](https://connect.systems-apps.com/__docs__/user/publishing-overview/)  
*(Sign in may be required — use your SSO or Connect account.)*

## Python version

Connect installs using **whatever Python environments your admins have registered** — the bundle pins a version via [`.python-version`](.python-version). If the build fails with *“no compatible Local environment … (available versions: …)”*, set `.python-version` to an **exact** entry from that list (e.g. `3.12.4` if that is all the server exposes).

Locally, **Python 3.10–3.12** works for this codebase; ideally your dev venv matches Connect’s interpreter for reproducible installs.

Verify after deploy:

```bash
python --version   # should match `.python-version` / Connect runtime
```

## What must ship with the bundle

Deploy from this folder (`HW2/`) — the bundle must include everything the app resolves at runtime **except secrets**:

| Include | Purpose |
|---------|---------|
| **`app/`** | Shiny UI (`app.py`, `www/`, …) |
| **`clinical_pipeline.py`**, **`functions.py`**, **`retrieval.py`**, **`dotenv_loader.py`** | Orchestration + OpenAI + SQL cohort tool |
| **`qc/`** | Validators, scoring, prompts, regrade helpers |
| **`clinical_rag_rules.yaml`** | Rules snippet in prompts |
| **`requirements.txt`** | Install list for Connect’s `pip` step |
| **`patients.db`** | **SQLite cohort** — unless you rely on **`PATIENTS_DB`** (absolute path **on the server**) |

**`patients.db`** is resolved as `HW2/patients.db` by default (see `clinical_pipeline.py`). If your database is hosted elsewhere on Connect storage, set environment variable **`PATIENTS_DB`** to that path and you can omit committing the DB in git (still upload/map it outside the repo per your security policy).

**Do not bundle** `.env` (contains API keys). Configure variables in the Connect **Vars** / **Environment** UI instead.

## Environment variables on Connect

| Variable | Required | Notes |
|----------|----------|--------|
| **`OPENAI_API_KEY`** | Yes | Set in Connect (never commit). |
| **`OPENAI_MODEL`** | No | Defaults to `gpt-4o-mini` in `functions.py` if unset. |
| **`PATIENTS_DB`** | No | Absolute path to `patients.db` on the server if not next to `clinical_pipeline.py`. |
| **`HW2_QC_TRIALS_APP`** | No | Shiny run batch size (default `1`). |

See also [`.env.example`](../.env.example) at the repo root for local naming.

## Typical deploy (command line)

From a machine with [rsconnect-python](https://docs.posit.co/rsconnect-python/) (or your org’s equivalent) and network access to Connect. Use **the same Python line as `.python-version`** for local `pip` installs when you care about reproducibility.

Do **not** paste shell comments (`# …`) onto the **same line** as `pip install`; `pip` can treat `#` as a bogus package name depending on quoting.

```bash
cd /path/to/DSAI-HW2/HW2
python3.12 -m venv .venv   # optional; replace 3.12 with your `.python-version` / Connect runtime
source .venv/bin/activate
python -m pip install -r requirements.txt rsconnect-python

rsconnect add --server https://connect.systems-apps.com --api-key YOUR_KEY --name systemsconnect

# Deploy with the `rsconnect` CLI (`python -m rsconnect` does not work — no package __main__).
# Interpreter version comes from `.python-version` (--override-python-version is deprecated).

# patients.db must be in this folder (or rely on PATIENTS_DB configured only on Connect)
rsconnect deploy shiny . \
  --entrypoint app.app:app \
  -n systemsconnect \
  --title "High Risk Patient Identifier"
```

Exact flags (`-n`, `--api-key`, manifests, etc.) follow your administrator’s checklist — see [Publishing overview](https://connect.systems-apps.com/__docs__/user/publishing-overview/) or the GUI “Publish” flow when you skip the CLI.

## Post-deploy checks

1. Open the published URL and confirm the header shows **AI ready** after you set **`OPENAI_API_KEY`** on the content item.
2. Run **Run analysis** once to confirm **`patients.db`** is reachable (no “patient records not found”).
3. Optional: create **`out/`** on first run automatically — QC panel reads `qc_summary` from the pipeline result in memory plus `HW2/out/` after runs.

If something fails only on Connect, compare **Logs** → standard error for missing files or denied paths to **`patients.db`**.
