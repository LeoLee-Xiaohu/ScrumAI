#!/usr/bin/env bash
# lint-prompts.sh — Validate prompt files in prompts/ directory
# Run: ./scripts/lint-prompts.sh
# Exit code 0 = all checks pass, 1 = failures found

set -euo pipefail

PROMPTS_DIR="prompts"
ERRORS=0

red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
warn()  { printf '\033[0;33m%s\033[0m\n' "$*"; }

check_fail() { red "  FAIL: $1"; ERRORS=$((ERRORS + 1)); }
check_pass() { green "  PASS: $1"; }

echo "=== Prompt Lint ==="
echo ""

# --- Check 1: prompts/ directory exists ---
if [ ! -d "$PROMPTS_DIR" ]; then
  check_fail "prompts/ directory not found"
  exit 1
fi

# --- Check 2: All files are .md ---
NON_MD=$(find "$PROMPTS_DIR" -maxdepth 1 -type f ! -name '*.md' 2>/dev/null)
if [ -n "$NON_MD" ]; then
  check_fail "Non-markdown files found in prompts/: $NON_MD"
else
  check_pass "All prompt files are .md"
fi

# --- Check 3: File naming convention (snake_case.md) ---
for f in "$PROMPTS_DIR"/*.md; do
  basename=$(basename "$f")
  if ! echo "$basename" | grep -qE '^[a-z][a-z0-9_]*\.md$'; then
    check_fail "$basename does not follow snake_case naming"
  fi
done
check_pass "File naming convention (snake_case.md)"

# --- Per-file checks ---
for f in "$PROMPTS_DIR"/*.md; do
  basename=$(basename "$f")
  echo ""
  echo "--- $basename ---"

  # Check 4: Not empty
  if [ ! -s "$f" ]; then
    check_fail "File is empty"
    continue
  fi
  check_pass "File is not empty"

  # Check 5: Minimum length (at least 200 chars for a meaningful prompt)
  CHARS=$(wc -c < "$f" | tr -d ' ')
  if [ "$CHARS" -lt 200 ]; then
    check_fail "File too short ($CHARS chars, minimum 200)"
  else
    check_pass "Length: $CHARS chars"
  fi

  # Check 6: Has a role definition (first line should define the AI's role)
  FIRST_LINE=$(head -1 "$f")
  if ! echo "$FIRST_LINE" | grep -qi "you are\|your role\|your task"; then
    warn "  WARN: First line may not define a role: '${FIRST_LINE:0:60}...'"
  else
    check_pass "Role definition in first line"
  fi

  # Check 7: Has structured output spec (JSON, format, or response keywords)
  if ! grep -qi 'json\|format\|response\|output\|return' "$f"; then
    check_fail "No output format specification found"
  else
    check_pass "Output format specified"
  fi

  # Check 8: No trailing whitespace on lines
  if grep -qP '\t' "$f" 2>/dev/null || grep -q '	' "$f"; then
    warn "  WARN: File contains tab characters (prefer spaces)"
  fi

  # Check 9: File ends with newline
  if [ -n "$(tail -c 1 "$f")" ]; then
    check_fail "File does not end with a newline"
  else
    check_pass "Ends with newline"
  fi
done

echo ""
echo "=== Summary ==="
if [ "$ERRORS" -gt 0 ]; then
  red "$ERRORS error(s) found"
  exit 1
else
  green "All checks passed"
  exit 0
fi
