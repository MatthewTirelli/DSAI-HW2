#!/usr/bin/env bash
# Create .venv if needed and pip install -r requirements.txt.
# Run from anywhere:  bash HW2/install_requirements.sh   OR   cd HW2 && ./install_requirements.sh

set -euo pipefail

HW2_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$HW2_DIR"

if [[ ! -d .venv ]]; then
  echo "Creating virtual environment in ${HW2_DIR}/.venv"
  python3 -m venv .venv
fi

echo "Installing packages from requirements.txt"
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo ""
echo "Done. Activate with:  source \"${HW2_DIR}/.venv/bin/activate\""
echo "Set OPENAI_API_KEY in ../.env (repo root) — see ../.env.example"
