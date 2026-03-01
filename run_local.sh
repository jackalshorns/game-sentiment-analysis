#!/bin/bash
# run_local.sh — one-time setup + run for sonar-prototype
# Run this from the sonar-prototype folder: bash run_local.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Sonar Prototype — Local Runner ==="
echo ""

# ── 1. Check Python ──────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Install Python 3.9+ from https://python.org"
  exit 1
fi
PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✓ Python $PYTHON_VER found"

# ── 2. Check API key ─────────────────────────────────────────────────────────
if [[ -z "$ANTHROPIC_API_KEY" ]]; then
  echo ""
  echo "ERROR: ANTHROPIC_API_KEY environment variable is not set."
  echo "  Set it with:  export ANTHROPIC_API_KEY=sk-ant-..."
  echo "  Or prefix:    ANTHROPIC_API_KEY=sk-ant-... bash run_local.sh"
  exit 1
fi
echo "✓ ANTHROPIC_API_KEY found"

# ── 3. Install dependencies ───────────────────────────────────────────────────
echo ""
echo "Installing dependencies..."
pip3 install -q -r requirements.txt
echo "✓ Dependencies installed"

# ── 4. Choose which script to run ────────────────────────────────────────────
echo ""
echo "Which pipeline do you want to run?"
echo "  1) analyze_expanded.py  (Steam + Reddit + YouTube, 165–356 insights/game)"
echo "  2) analyze_sonar.py     (Press + Reddit + YouTube, 10–50 insights/game)"
echo "  3) Both (sequentially)"
echo ""
read -rp "Enter 1, 2, or 3 [default: 1]: " CHOICE
CHOICE="${CHOICE:-1}"

run_script() {
  local script="$1"
  echo ""
  echo "▶ Running $script ..."
  echo "  (this takes 15–25 minutes for all 10 games)"
  echo "  Logs will stream below. Reports save to reports_expanded/ or reports_sonar/"
  echo ""
  python3 -u "$script"
  echo ""
  echo "✓ $script complete"
}

case "$CHOICE" in
  1) run_script "analyze_expanded.py" ;;
  2) run_script "analyze_sonar.py" ;;
  3)
    run_script "analyze_expanded.py"
    run_script "analyze_sonar.py"
    ;;
  *)
    echo "Invalid choice. Run the script directly: python3 analyze_expanded.py"
    exit 1
    ;;
esac

echo ""
echo "=== Done! Open the reports in your browser: ==="
[[ "$CHOICE" == "1" || "$CHOICE" == "3" ]] && echo "  reports_expanded/index.html"
[[ "$CHOICE" == "2" || "$CHOICE" == "3" ]] && echo "  reports_sonar/index.html"
