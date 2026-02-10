"""Issue readiness scoring runner.

Mirrors the scoring system in scrumai-forge:
- src/lib/issue-scorer.ts (scoring prompt + API call + parsing)
- src/resolvers/config.ts (LLM config)

Usage:
    python main.py score -f ticket.md
    python main.py score -t "Build a login page with email/password auth"
"""

import json
import logging

from client import LLMClient, load_prompt, parse_structured_response
from models.scoring import ScoreResult

logger = logging.getLogger(__name__)

# ANSI color codes
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _display_score_result(result: ScoreResult) -> None:
    """Display a score result with a visual bar and dimension breakdown."""
    total = result.totalScore
    bar_filled = "█" * total
    bar_empty = "░" * (10 - total)

    if total >= 7:
        color = GREEN
        status = "Ready for development"
    elif total >= 4:
        color = YELLOW
        status = "Needs improvement"
    else:
        color = RED
        status = "Not ready"

    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  Issue Readiness Score{RESET}")
    print(f"{'═' * 60}")

    print(f"\n  {BOLD}Overall:{RESET} {color}{bar_filled}{bar_empty} {total}/10{RESET} - {status}")

    # Dimension breakdown
    print(f"\n  {BOLD}Dimensions:{RESET}")
    dims = result.dimensions
    for name, dim in [
        ("Runtime Target", dims.runtimeTarget),
        ("Delivery Form", dims.deliveryForm),
        ("Control Scheme", dims.controlScheme),
        ("Business Rules", dims.businessRules),
        ("Acceptance Criteria", dims.acceptanceCriteria),
    ]:
        score = dim.score
        dim_bar = "█" * score + "░" * (2 - score)
        dim_color = GREEN if score == 2 else (YELLOW if score == 1 else RED)
        print(f"    {dim_color}{dim_bar}{RESET} {name}: {score}/2 - {DIM}{dim.reason}{RESET}")

    print(f"\n  {BOLD}Summary:{RESET} {result.summary}")


def run_scoring(client: LLMClient, issue_text: str) -> None:
    """Score an issue for readiness.

    Mirrors: scoreIssue() in src/lib/issue-scorer.ts
    """
    system_prompt = load_prompt("issue_scoring")

    user_message = f"Please score this issue:\n\n{issue_text}"

    print(f"\n{DIM}  Scoring issue...{RESET}", end="", flush=True)

    raw_response = client.chat(system_prompt, [{"role": "user", "content": user_message}])
    print("\r" + " " * 40 + "\r", end="")

    try:
        result = parse_structured_response(raw_response, ScoreResult)
    except ValueError as e:
        logger.error("Failed to parse scoring response: %s", e)
        print(f"\n{RED}Error: Failed to parse response{RESET}")
        print(f"{DIM}{raw_response[:500]}{RESET}")
        return

    _display_score_result(result)

    # Save structured output
    output_file = "score_result.json"
    with open(output_file, "w") as f:
        json.dump(result.model_dump(), f, indent=2, ensure_ascii=False)
    print(f"\n  {DIM}Structured output saved to: {output_file}{RESET}\n")
