#!/usr/bin/env bash
# test-lint-prompts.sh — Unit tests for lint-prompts.sh
# Verifies that the linter correctly passes valid prompts and rejects invalid ones.
#
# Run: ./scripts/test-lint-prompts.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LINT_SCRIPT="$SCRIPT_DIR/lint-prompts.sh"
TEST_DIR=$(mktemp -d)
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

cleanup() { rm -rf "$TEST_DIR"; }
trap cleanup EXIT

red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }

# Run lint in a temp dir with a custom prompts/ directory
# Usage: run_lint <test_prompts_dir>
# Returns: exit code from lint script
run_lint() {
  local workdir="$1"
  (cd "$workdir" && bash "$LINT_SCRIPT" > /dev/null 2>&1)
  return $?
}

assert_pass() {
  local name="$1" workdir="$2"
  TESTS_RUN=$((TESTS_RUN + 1))
  if run_lint "$workdir"; then
    green "  PASS: $name"
    TESTS_PASSED=$((TESTS_PASSED + 1))
  else
    red "  FAIL: $name (expected pass, got fail)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
  fi
}

assert_fail() {
  local name="$1" workdir="$2"
  TESTS_RUN=$((TESTS_RUN + 1))
  if run_lint "$workdir"; then
    red "  FAIL: $name (expected fail, got pass)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
  else
    green "  PASS: $name"
    TESTS_PASSED=$((TESTS_PASSED + 1))
  fi
}

# Helper: create a valid prompt file
create_valid_prompt() {
  local dir="$1" name="${2:-test_prompt.md}"
  mkdir -p "$dir/prompts"
  cat > "$dir/prompts/$name" <<'PROMPT'
You are an expert assistant. Your role is to help users with tasks.

## Your Task

Analyze the input and provide structured feedback.

## Output Format

Respond in JSON format with the following structure:

```json
{
  "analysis": "string",
  "score": 0,
  "recommendations": ["string"]
}
```

Ensure your response is valid JSON and follows the schema above. Be thorough in your analysis and provide actionable recommendations based on the input context.
PROMPT
}

echo "=== Lint Script Unit Tests ==="
echo ""

# ----- TEST GROUP 1: Valid prompts should pass -----
echo "--- Valid Cases (should pass) ---"

# Test 1: Single valid prompt
DIR1="$TEST_DIR/test1"
create_valid_prompt "$DIR1"
assert_pass "Single valid prompt file" "$DIR1"

# Test 2: Multiple valid prompts
DIR2="$TEST_DIR/test2"
create_valid_prompt "$DIR2" "brainstorm.md"
create_valid_prompt "$DIR2" "scoring.md"
create_valid_prompt "$DIR2" "role_dispatch.md"
assert_pass "Multiple valid prompt files" "$DIR2"

# Test 3: Prompt with snake_case and numbers
DIR3="$TEST_DIR/test3"
create_valid_prompt "$DIR3" "task_decomposition_v2.md"
assert_pass "Snake_case with numbers" "$DIR3"

echo ""

# ----- TEST GROUP 2: Invalid prompts should fail -----
echo "--- Invalid Cases (should fail) ---"

# Test 4: No prompts directory
DIR4="$TEST_DIR/test4"
mkdir -p "$DIR4"
assert_fail "Missing prompts/ directory" "$DIR4"

# Test 5: Empty file
DIR5="$TEST_DIR/test5"
mkdir -p "$DIR5/prompts"
touch "$DIR5/prompts/empty.md"
assert_fail "Empty prompt file" "$DIR5"

# Test 6: File too short
DIR6="$TEST_DIR/test6"
mkdir -p "$DIR6/prompts"
echo "You are a helper." > "$DIR6/prompts/short.md"
assert_fail "Prompt file too short (<200 chars)" "$DIR6"

# Test 7: Non-markdown file in prompts/
DIR7="$TEST_DIR/test7"
create_valid_prompt "$DIR7"
echo "not a prompt" > "$DIR7/prompts/notes.txt"
assert_fail "Non-markdown file in prompts/" "$DIR7"

# Test 8: Bad file naming (uppercase)
DIR8="$TEST_DIR/test8"
mkdir -p "$DIR8/prompts"
create_valid_prompt "$DIR8" "valid.md"
# Create a file with uppercase name
cp "$DIR8/prompts/valid.md" "$DIR8/prompts/BadName.md"
assert_fail "Uppercase in filename (BadName.md)" "$DIR8"

# Test 9: Bad file naming (hyphen)
DIR9="$TEST_DIR/test9"
mkdir -p "$DIR9/prompts"
cp "$DIR8/prompts/valid.md" "$DIR9/prompts/bad-name.md"
assert_fail "Hyphen in filename (bad-name.md)" "$DIR9"

# Test 10: No output format keywords
DIR10="$TEST_DIR/test10"
mkdir -p "$DIR10/prompts"
cat > "$DIR10/prompts/no_output.md" <<'PROMPT'
You are an expert assistant who helps users think through problems.

## Your Approach

Use the Socratic method to guide users through their thinking process.
Ask probing questions that challenge assumptions and reveal hidden complexity.

Continue the dialogue until the user reaches a clear understanding of their problem space. Help them identify the key constraints and trade-offs involved in their decision.

Maintain a supportive and encouraging tone throughout the conversation.
PROMPT
assert_fail "No output format specification" "$DIR10"

# Test 11: File without trailing newline
DIR11="$TEST_DIR/test11"
mkdir -p "$DIR11/prompts"
printf 'You are an expert. Your role is to analyze tasks.\n\nProvide structured JSON output with analysis, score, and recommendations.\n\nEnsure completeness and accuracy in all responses. Follow the schema provided and validate your output before returning it to the user for review and action.' > "$DIR11/prompts/no_newline.md"
assert_fail "File without trailing newline" "$DIR11"

echo ""

# ----- TEST GROUP 3: Edge cases -----
echo "--- Edge Cases ---"

# Test 12: Prompt with "Your task" instead of "You are"
DIR12="$TEST_DIR/test12"
mkdir -p "$DIR12/prompts"
cat > "$DIR12/prompts/alt_role.md" <<'PROMPT'
Your task is to evaluate the quality of software requirements.

## Evaluation Criteria

Score each requirement on clarity, completeness, and testability.

## Output Format

Respond in JSON format:

```json
{
  "scores": [{"dimension": "string", "score": 0, "rationale": "string"}],
  "total": 0,
  "summary": "string"
}
```

Be precise and reference specific parts of the requirement in your rationale. Provide actionable feedback for improvement.
PROMPT
assert_pass "Alternative role definition ('Your task')" "$DIR12"

# Test 13: Valid prompt on real prompts directory
DIR13="$TEST_DIR/test13"
if [ -d "$SCRIPT_DIR/../prompts" ]; then
  cp -r "$SCRIPT_DIR/../prompts" "$DIR13/prompts" 2>/dev/null || true
  if [ -d "$DIR13/prompts" ]; then
    assert_pass "Real prompts/ directory from repo" "$DIR13"
  fi
fi

echo ""
echo "=== Results ==="
echo "Tests run: $TESTS_RUN"
echo "Passed:    $TESTS_PASSED"
echo "Failed:    $TESTS_FAILED"

if [ "$TESTS_FAILED" -gt 0 ]; then
  red "SOME TESTS FAILED"
  exit 1
else
  green "ALL TESTS PASSED"
  exit 0
fi
