"""Interactive brainstorm runner.

Mirrors the 4-phase Socratic dialogue in scrumai-forge:
- src/lib/brainstorm-prompts.ts (system prompt + initial prompt)
- src/resolvers/brainstorm.ts (session management)
- src/frontend/components/brainstorm/ (wizard UI)

Usage:
    python main.py brainstorm
    python main.py brainstorm -f ticket.md
    python main.py brainstorm -t "Build a REST API for user management"
"""

import json
import logging
import sys

from client import LLMClient, load_prompt, parse_structured_response
from models.brainstorm import BrainstormResponse

logger = logging.getLogger(__name__)

# ANSI color codes
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _build_initial_user_message(context: str | None) -> str:
    """Build the initial user message for the brainstorm session.

    Mirrors: getInitialPrompt() in src/lib/brainstorm-prompts.ts
    """
    if context:
        return (
            "You are starting a new brainstorming session.\n\n"
            "The user has created a JIRA ticket with the following information:\n\n"
            f"---\n{context}\n---\n\n"
            "Analyze this ticket information carefully. Based on what is provided:\n"
            "- Identify what is already clear and well-defined\n"
            "- Identify what is missing or ambiguous\n"
            '- Do NOT ask a generic opener like "What would you like to brainstorm today?"\n'
            "- Begin Phase 1 by acknowledging the ticket context, summarizing your "
            "understanding, and asking your first clarifying question.\n\n"
            "Remember:\n"
            "- Respond ONLY in valid JSON format matching BrainstormResponse\n"
            "- Ask ONE question at a time\n"
            '- Provide EXACTLY 3 structured option objects (label/description/value). Do NOT include "Other".\n'
            "- Auto-detect and match the user's language\n"
            "- Include scoring in EVERY response\n"
            "- When scoring.total >= 7, set isComplete: true and generate the final prompt\n\n"
            "Start now."
        )
    return (
        "You are starting a new brainstorming session.\n\n"
        "Begin Phase 1 by asking the user what they would like to brainstorm today.\n\n"
        "Remember:\n"
        "- Respond ONLY in valid JSON format matching BrainstormResponse\n"
        "- Ask ONE question at a time\n"
        '- Provide EXACTLY 3 structured option objects (label/description/value). Do NOT include "Other".\n'
        "- Auto-detect and match the user's language\n"
        "- Include scoring in EVERY response\n"
        "- When scoring.total >= 7, set isComplete: true and generate the final prompt\n\n"
        "Start now."
    )


def _display_scoring(scoring: dict | None) -> None:
    """Display the requirement clarity scoring bar."""
    if not scoring:
        return

    total = scoring.get("total", 0)
    bar_filled = "█" * total
    bar_empty = "░" * (10 - total)

    if total >= 7:
        color = GREEN
        status = "Ready!"
    elif total >= 4:
        color = YELLOW
        status = "Getting there..."
    else:
        color = RED
        status = "Needs more clarity"

    print(f"\n  {DIM}Clarity Score:{RESET} {color}{bar_filled}{bar_empty} {total}/10{RESET} {status}")
    print(
        f"  {DIM}Goal {scoring.get('taskGoal', 0)}/3 | "
        f"Criteria {scoring.get('completionCriteria', 0)}/3 | "
        f"Scope {scoring.get('scope', 0)}/2 | "
        f"Constraints {scoring.get('constraints', 0)}/2{RESET}"
    )

    low = scoring.get("lowScoreDimensions", [])
    if low:
        print(f"  {YELLOW}Weak areas: {', '.join(low)}{RESET}")


def _display_options(options: list[dict]) -> None:
    """Display brainstorm options."""
    for i, opt in enumerate(options, 1):
        label = opt.get("label", f"Option {i}")
        desc = opt.get("description", "")
        print(f"  {CYAN}{i}.{RESET} {BOLD}{label}{RESET}")
        if desc:
            print(f"     {DIM}{desc}{RESET}")

    # Always show "Other" option
    print(f"  {CYAN}{len(options) + 1}.{RESET} {BOLD}Other{RESET} - Provide your own response")


def _display_phase(phase: int) -> None:
    """Display the current brainstorm phase."""
    phase_names = {1: "Context", 2: "Explore", 3: "Solution", 4: "Testing"}
    phases_display = []
    for p in range(1, 5):
        name = phase_names[p]
        if p == phase:
            phases_display.append(f"{GREEN}[{name}]{RESET}")
        elif p < phase:
            phases_display.append(f"{DIM}[{name}]{RESET}")
        else:
            phases_display.append(f"{DIM} {name} {RESET}")
    print(f"\n{'─' * 60}")
    print(f"  Phase: {' → '.join(phases_display)}")


def _format_user_answer(selected_indices: list[int], other_text: str, options: list[dict]) -> str:
    """Format the user's answer for the conversation.

    Mirrors: formatAnswerAsMessage() in src/resolvers/brainstorm.ts
    """
    parts: list[str] = []
    for idx in selected_indices:
        if 0 <= idx < len(options):
            opt = options[idx]
            parts.append(f"Selected: {opt.get('label', '')} ({opt.get('value', '')})")
    if other_text:
        parts.append(f"Other: {other_text}")
    return "\n".join(parts) if parts else "No selection"


def run_brainstorm(client: LLMClient, context: str | None = None) -> None:
    """Run an interactive brainstorm session.

    This mirrors the full brainstorm flow from scrumai-forge:
    1. Start session with system prompt
    2. Loop: display question → get user answer → send to LLM
    3. Display scoring after each response
    4. End when isComplete is true
    """
    system_prompt = load_prompt("brainstorm")
    initial_message = _build_initial_user_message(context)
    conversation: list[dict[str, str]] = [{"role": "user", "content": initial_message}]

    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  ScrumAI Brainstorm Playground{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}")
    if context:
        preview = context[:100] + "..." if len(context) > 100 else context
        print(f"  {DIM}Context: {preview}{RESET}")

    round_num = 0
    while True:
        round_num += 1
        print(f"\n{DIM}  Thinking...{RESET}", end="", flush=True)

        raw_response = client.chat(system_prompt, conversation)
        print("\r" + " " * 40 + "\r", end="")  # Clear "Thinking..."

        try:
            response = parse_structured_response(raw_response, BrainstormResponse)
            response_dict = response.model_dump(exclude_none=True)
        except ValueError:
            # Fallback: try to display raw response
            logger.warning("Failed to parse structured response, showing raw output")
            print(f"\n{YELLOW}Raw response:{RESET}\n{raw_response}")
            conversation.append({"role": "assistant", "content": raw_response})
            user_input = input(f"\n{CYAN}Your response:{RESET} ").strip()
            if user_input.lower() in ("quit", "exit", "q"):
                break
            conversation.append({"role": "user", "content": user_input})
            continue

        # Store assistant response
        conversation.append({"role": "assistant", "content": raw_response})

        # Check if complete
        if response.isComplete:
            print(f"\n{GREEN}{'═' * 60}{RESET}")
            print(f"{GREEN}{BOLD}  Brainstorm Complete!{RESET}")
            print(f"{GREEN}{'═' * 60}{RESET}")

            if response.summary:
                s = response.summary
                print(f"\n  {BOLD}Task Overview:{RESET} {s.taskOverview}")
                print(f"  {BOLD}Background:{RESET} {s.background}")
                print(f"  {BOLD}Core Features:{RESET}")
                for feat in s.coreFeatures:
                    print(f"    - {feat}")
                print(f"  {BOLD}Technical Requirements:{RESET}")
                for req in s.technicalRequirements:
                    print(f"    - {req}")
                print(f"  {BOLD}Testing Plan:{RESET} {s.testingPlan}")
                print(f"  {BOLD}Success Criteria:{RESET}")
                for crit in s.successCriteria:
                    print(f"    - {crit}")

            if response.generatedPrompt:
                print(f"\n{BOLD}{'─' * 60}{RESET}")
                print(f"{BOLD}  Generated Prompt:{RESET}")
                print(f"{'─' * 60}")
                print(response.generatedPrompt)
                print(f"{'─' * 60}")

            _display_scoring(
                response.scoring.model_dump() if response.scoring else None
            )

            # Output final JSON
            print(f"\n{DIM}  Structured output saved to: brainstorm_result.json{RESET}")
            with open("brainstorm_result.json", "w") as f:
                json.dump(response_dict, f, indent=2, ensure_ascii=False)
            break

        # Display phase and question
        _display_phase(response.phase)
        _display_scoring(
            response.scoring.model_dump() if response.scoring else None
        )

        if response.context:
            print(f"\n  {DIM}{response.context}{RESET}")

        print(f"\n  {BOLD}{response.question}{RESET}\n")

        options = [opt.model_dump() for opt in response.options]
        _display_options(options)

        # Get user input
        print()
        user_input = input(f"  {CYAN}Your choice (numbers, comma-separated, or text):{RESET} ").strip()

        if user_input.lower() in ("quit", "exit", "q"):
            print(f"\n{DIM}  Session abandoned.{RESET}")
            break

        # Parse user selection
        selected_indices: list[int] = []
        other_text = ""

        # Check if it's a number selection
        parts = [p.strip() for p in user_input.split(",")]
        for part in parts:
            try:
                idx = int(part) - 1
                if idx == len(options):  # "Other" option
                    other_text = input(f"  {CYAN}Your custom response:{RESET} ").strip()
                elif 0 <= idx < len(options):
                    selected_indices.append(idx)
                else:
                    # Treat as free text
                    other_text = user_input
                    break
            except ValueError:
                # Free text input
                other_text = user_input
                break

        answer = _format_user_answer(selected_indices, other_text, options)
        conversation.append({"role": "user", "content": answer})

    print()
