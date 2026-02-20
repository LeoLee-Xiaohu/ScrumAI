"""Role dispatch runner.

Reads decomposed tasks and assigns roles + autonomy levels using a two-step
LLM evaluation framework:
  Step 1: 3-dimension delegation scoring → autonomy_level + owner_type
  Step 2: Role classification → recommended_role

Usage:
    python main.py dispatch
    python main.py dispatch -f decomposed_task.json
    python main.py dispatch -f tasks.json -o dispatched.json
"""

import json
import logging
from pathlib import Path

from client import LLMClient, load_prompt, parse_structured_response
from models.role import DispatchResult, ALL_ROLES

logger = logging.getLogger(__name__)

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

ROLE_COLORS = {
    "Junior Developer": GREEN,
    "Senior Developer": CYAN,
    "Product Owner": MAGENTA,
    "Scrum Master": YELLOW,
    "Reviewer": RED,
}

AUTONOMY_ICONS = {
    "autonomous": f"{GREEN}⚡{RESET}",
    "supervised": f"{YELLOW}👁{RESET}",
    "manual": f"{RED}✋{RESET}",
}


def _extract_tasks_for_prompt(data: dict) -> list[dict]:
    """Extract task fields relevant for dispatch from decomposed output."""
    tasks = []
    for story in data.get("stories", []):
        for task in story.get("tasks", []):
            tasks.append({
                "task_id": task["task_id"],
                "title": task["title"],
                "description": task.get("description", ""),
                "dependencies": task.get("dependencies", []),
                "acceptance_criteria": task.get("acceptance_criteria", ""),
            })
    return tasks


def _display_dispatch(result: DispatchResult) -> None:
    """Display dispatch results with color-coded roles and score bars."""
    print(f"\n{BOLD}{'═' * 70}{RESET}")
    print(f"{BOLD}  Role Dispatch Results{RESET}")
    print(f"{'═' * 70}")

    ai_count = sum(1 for d in result.dispatches if d.owner_type == "ai")
    human_count = sum(1 for d in result.dispatches if d.owner_type == "human")
    print(f"\n  {DIM}Tasks: {len(result.dispatches)} total | "
          f"AI: {ai_count} | Human: {human_count}{RESET}")

    for d in result.dispatches:
        role_color = ROLE_COLORS.get(d.recommended_role, DIM)
        auto_icon = AUTONOMY_ICONS.get(d.autonomy_level, "?")

        score_bar = ""
        for dim_name, dim in [
            ("C", d.scoring.complexity),
            ("R", d.scoring.risk),
            ("H", d.scoring.human_judgment),
        ]:
            s = dim.score
            c = GREEN if s == 0 else (YELLOW if s == 1 else RED)
            score_bar += f"{c}{'█' * s}{'░' * (2 - s)}{RESET} "

        print(f"\n  {BOLD}{d.task_id}{RESET}: {role_color}{d.recommended_role}{RESET} "
              f"{auto_icon} {d.autonomy_level}")
        print(f"    Score: [{score_bar}] {d.total_score}/6 "
              f"({d.owner_type})")
        print(f"    {DIM}{d.reasoning}{RESET}")

    print(f"\n  {BOLD}Summary:{RESET} {result.summary}")


def run_dispatch(
    client: LLMClient,
    input_file: str = "decomposed_task.json",
    output_file: str = "dispatched_task.json",
) -> None:
    """Dispatch roles for decomposed tasks.

    Reads decomposed_task.json, sends tasks to LLM for evaluation,
    and saves role assignments to dispatched_task.json.
    """
    input_path = Path(input_file)
    if not input_path.exists():
        logger.error("Input file not found: %s", input_file)
        print(f"\n{RED}Error: File not found: {input_file}{RESET}")
        print(f"{DIM}Run 'python main.py decompose' first to generate tasks.{RESET}")
        return

    with open(input_path) as f:
        data = json.load(f)

    tasks = _extract_tasks_for_prompt(data)
    if not tasks:
        print(f"\n{RED}Error: No tasks found in {input_file}{RESET}")
        return

    print(f"\n{DIM}  Found {len(tasks)} tasks in {input_file}{RESET}")

    prompt_template = load_prompt("role_dispatch")
    tasks_json = json.dumps(tasks, indent=2, ensure_ascii=False)
    system_prompt = prompt_template.replace("{tasks_json}", tasks_json)

    print(f"{DIM}  Dispatching roles...{RESET}", end="", flush=True)

    raw_response = client.chat(
        system_prompt,
        [{"role": "user", "content": f"Dispatch roles for these {len(tasks)} tasks."}],
    )
    print("\r" + " " * 40 + "\r", end="")

    try:
        result = parse_structured_response(raw_response, DispatchResult)
    except ValueError as e:
        logger.error("Failed to parse dispatch response: %s", e)
        print(f"\n{RED}Error: Failed to parse response{RESET}")
        print(f"{DIM}{raw_response[:500]}{RESET}")
        return

    _display_dispatch(result)

    with open(output_file, "w") as f:
        json.dump(result.model_dump(), f, indent=2, ensure_ascii=False)
    print(f"\n  {DIM}Dispatch results saved to: {output_file}{RESET}\n")
