#!/usr/bin/env bash
# Pull the Ollama model used by Homework 2 (default: llama3.2).
# Run from anywhere:  bash HW2/setup_hw2.sh   OR   cd HW2 && ./setup_hw2.sh
#
# Override model:  OLLAMA_MODEL=llama3.2:latest ./setup_hw2.sh

set -euo pipefail

HW2_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$HW2_DIR"

MODEL="${OLLAMA_MODEL:-llama3.2}"

if ! command -v ollama >/dev/null 2>&1; then
  echo "Error: 'ollama' not found. Install from https://ollama.com and ensure it is on your PATH."
  exit 1
fi

echo "Homework 2 — pulling Ollama model: ${MODEL}"
echo "(Set OLLAMA_MODEL to use a different tag; clinical_pipeline.py reads the same env var.)"
ollama pull "${MODEL}"

echo ""
echo "OK — model pulled. Next steps:"
echo "  1. cd \"${HW2_DIR}\""
echo "  2. python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\\Scripts\\activate"
echo "  3. pip install -r requirements.txt"
echo "  4. shiny run app/app.py --reload   # or: python clinical_pipeline.py"
echo ""
echo "Optional: ensure patients.db exists (see README). Quick check:"
echo "  curl -s http://127.0.0.1:11434/api/tags | head -c 200"
