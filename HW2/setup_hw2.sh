#!/usr/bin/env bash
# HW2 historically used local model runners — this bundle now relies on OpenAI.
# Configure secrets in `.env` at the repository ROOT (parent of this folder), or in HW2/.env — see HW2/dotenv_loader.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "Homework 2 — OpenAI-based pipeline"
echo "  cp \"${REPO_ROOT}/.env.example\" \"${REPO_ROOT}/.env\""
echo "  Edit .env at repo root and set OPENAI_API_KEY=..."
echo "  optional: OPENAI_MODEL=gpt-4o-mini in that same file"
echo "Install Python deps: cd \"${SCRIPT_DIR}\" && pip install -r requirements.txt"
exit 0
