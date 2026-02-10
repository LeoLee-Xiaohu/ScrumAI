"""Task decomposition runner.

Mirrors the existing main.py functionality with structured output validation.

Usage:
    python main.py decompose -f goal.md
    python main.py decompose -t "Build a REST API for user management"
"""

import json
import logging

from client import LLMClient, load_prompt, parse_structured_response
from models.task import TaskDecompositionResult

logger = logging.getLogger(__name__)

# ANSI color codes
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _display_decomposition(result: TaskDecompositionResult) -> None:
    """Display the decomposition result as a tree view."""
    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  Task Decomposition{RESET}")
    print(f"{'═' * 60}")

    # Epic
    print(f"\n  {BOLD}Epic:{RESET} {result.epic.title}")
    print(f"  {DIM}{result.epic.description}{RESET}")

    # Reasoning
    print(f"\n  {BOLD}Analysis:{RESET}")
    print(f"  {DIM}{result.reasoning[:300]}{'...' if len(result.reasoning) > 300 else ''}{RESET}")

    # Stories and Tasks
    for story in result.stories:
        print(f"\n  {CYAN}┌─ {story.id}: {story.title}{RESET}")
        for i, task in enumerate(story.tasks):
            is_last = i == len(story.tasks) - 1
            prefix = "└─" if is_last else "├─"
            connector = "  " if is_last else "│ "

            # Status icon
            status_icons = {
                "todo": f"{DIM}○{RESET}",
                "in_progress": f"{YELLOW}◉{RESET}",
                "blocked": f"{RED}✕{RESET}",
                "done": f"{GREEN}✓{RESET}",
            }
            icon = status_icons.get(task.status, "○")

            # Role color
            role_color = GREEN if task.owner_type == "ai" else YELLOW
            role_label = f"{role_color}{task.role}{RESET}"

            print(f"  {CYAN}│ {prefix}{RESET} {icon} {task.task_id}: {task.title}")
            print(
                f"  {CYAN}│ {connector}{RESET}   {DIM}Role: {RESET}{role_label} "
                f"{DIM}| Est: {task.estimate_hours or '?'}h | SP: {task.story_points or '?'}{RESET}"
            )

            if task.dependencies:
                deps = ", ".join(task.dependencies)
                print(f"  {CYAN}│ {connector}{RESET}   {DIM}Deps: {deps}{RESET}")

    # Execution Plan
    plan = result.execution_plan
    print(f"\n  {BOLD}Execution Plan:{RESET}")
    for phase in plan.phases:
        tasks_str = ", ".join(phase.parallel_tasks)
        print(f"    Phase {phase.phase}: {phase.description}")
        print(f"    {DIM}Parallel: [{tasks_str}]{RESET}")

    print(f"\n  {BOLD}Total Estimated Hours:{RESET} {plan.total_estimated_hours}h")
    print(f"  {BOLD}Critical Path:{RESET} {' → '.join(plan.critical_path)}")


def run_decomposition(client: LLMClient, task_description: str) -> None:
    """Decompose a high-level goal into sub-tasks.

    Mirrors: decompose_task() in original main.py, with Pydantic validation.
    """
    prompt_template = load_prompt("task_decomposition")
    # The task_decomposition prompt uses {task_description} placeholder
    system_prompt = prompt_template.format(task_description=task_description)

    print(f"\n{DIM}  Decomposing task...{RESET}", end="", flush=True)

    # For task decomposition, the full prompt is the system message
    raw_response = client.chat(system_prompt, [{"role": "user", "content": task_description}])
    print("\r" + " " * 40 + "\r", end="")

    try:
        result = parse_structured_response(raw_response, TaskDecompositionResult)
    except ValueError as e:
        logger.error("Failed to parse decomposition response: %s", e)
        print(f"\n{RED}Error: Failed to parse response{RESET}")
        print(f"{DIM}{raw_response[:500]}{RESET}")
        return

    _display_decomposition(result)

    # Save structured output
    output_file = "decomposed_task.json"
    with open(output_file, "w") as f:
        json.dump(result.model_dump(), f, indent=2, ensure_ascii=False)
    print(f"\n  {DIM}Structured output saved to: {output_file}{RESET}\n")
