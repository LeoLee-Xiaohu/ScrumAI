"""Dispatch evaluation runner.

Evaluates the accuracy of role dispatch results by comparing them to
original task assignments and validating scoring framework consistency.

Usage:
    uv run main.py evaluate-dispatch
    uv run main.py evaluate-dispatch -i decomposed_task.json -d dispatched_task.json
    uv run main.py evaluate-dispatch -i tasks.json -d dispatch.json -o evaluation.json
"""

import json
import logging
from pathlib import Path

from client import LLMClient, load_prompt, parse_structured_response
from models.dispatch_evaluation import DispatchEvaluationResult
from models.role import AI_ROLES, ALL_ROLES, HUMAN_ROLES

logger = logging.getLogger(__name__)

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _display_evaluation(result: DispatchEvaluationResult) -> None:
    """Display evaluation results with color-coded metrics."""
    print(f"\n{BOLD}{'═' * 80}{RESET}")
    print(f"{BOLD}  Dispatch Evaluation Results{RESET}")
    print(f"{'═' * 80}")

    # Overall metrics
    m = result.overall_metrics
    print(f"\n{BOLD}Overall Metrics:{RESET}")
    print(f"  Total Tasks: {m.total_tasks}")
    
    def _color_metric(value: float, good_threshold: float = 85.0) -> str:
        color = GREEN if value >= good_threshold else (YELLOW if value >= 70.0 else RED)
        return f"{color}{value:.1f}%{RESET}"
    
    print(f"  Owner Type Accuracy:          {_color_metric(m.owner_type_accuracy)}")
    print(f"  Role Precision:               {_color_metric(m.role_precision)}")
    print(f"  Dimension Alignment:          {_color_metric(m.dimension_alignment)}")
    print(f"  Autonomy Mapping Correctness: {_color_metric(m.autonomy_mapping_correctness)}")
    print(f"  Avg Reasoning Quality:        {CYAN}{m.avg_reasoning_quality:.1f}/3.0{RESET}")

    # Owner type analysis
    ota = result.owner_type_analysis
    if ota.false_ai_assignments or ota.false_human_assignments:
        print(f"\n{BOLD}Owner Type Issues:{RESET}")
        
        if ota.false_ai_assignments:
            print(f"\n  {RED}False AI Assignments:{RESET} ({len(ota.false_ai_assignments)})")
            for issue in ota.false_ai_assignments:
                severity_color = RED if issue.severity == "high" else (YELLOW if issue.severity == "medium" else DIM)
                print(f"    {severity_color}[{issue.severity.upper()}]{RESET} {BOLD}{issue.task_id}{RESET}")
                print(f"      {issue.issue}")
        
        if ota.false_human_assignments:
            print(f"\n  {YELLOW}False Human Assignments:{RESET} ({len(ota.false_human_assignments)})")
            for issue in ota.false_human_assignments:
                severity_color = RED if issue.severity == "high" else (YELLOW if issue.severity == "medium" else DIM)
                print(f"    {severity_color}[{issue.severity.upper()}]{RESET} {BOLD}{issue.task_id}{RESET}")
                print(f"      {issue.issue}")

    # Role analysis
    ra = result.role_analysis
    if ra.notable_issues:
        print(f"\n{BOLD}Role Assignment Issues:{RESET} ({len(ra.notable_issues)})")
        for issue in ra.notable_issues:
            print(f"  {BOLD}{issue.task_id}{RESET}: {issue.dispatched_role} → {CYAN}{issue.suggested_role}{RESET}")
            print(f"    {DIM}{issue.reason}{RESET}")

    # Dimension score analysis
    dsa = result.dimension_score_analysis
    if dsa.over_scored_tasks or dsa.under_scored_tasks:
        print(f"\n{BOLD}Dimension Score Issues:{RESET}")
        
        if dsa.over_scored_tasks:
            print(f"\n  {YELLOW}Over-Scored:{RESET} ({len(dsa.over_scored_tasks)})")
            for issue in dsa.over_scored_tasks:
                print(f"    {BOLD}{issue.task_id}{RESET} {issue.dimension}: {issue.given_score} → {issue.suggested_score}")
                print(f"      {DIM}{issue.reason}{RESET}")
        
        if dsa.under_scored_tasks:
            print(f"\n  {YELLOW}Under-Scored:{RESET} ({len(dsa.under_scored_tasks)})")
            for issue in dsa.under_scored_tasks:
                print(f"    {BOLD}{issue.task_id}{RESET} {issue.dimension}: {issue.given_score} → {issue.suggested_score}")
                print(f"      {DIM}{issue.reason}{RESET}")

    # Autonomy mapping issues
    if result.autonomy_mapping_issues:
        print(f"\n{BOLD}{RED}Autonomy Mapping Errors:{RESET} ({len(result.autonomy_mapping_issues)})")
        for issue in result.autonomy_mapping_issues:
            print(f"  {BOLD}{issue.task_id}{RESET} (score={issue.total_score})")
            print(f"    Expected: {issue.expected_autonomy} / {issue.expected_owner}")
            print(f"    Actual:   {RED}{issue.actual_autonomy} / {issue.actual_owner}{RESET}")

    # Recommendations
    if result.recommendations:
        print(f"\n{BOLD}Recommendations:{RESET}")
        for i, rec in enumerate(result.recommendations, 1):
            print(f"  {i}. {rec}")

    # Summary
    print(f"\n{BOLD}Summary:{RESET}")
    print(f"  {result.summary}")
    print()


def run_evaluation(
    client: LLMClient,
    decomposed_file: str = "decomposed_task.json",
    dispatched_file: str = "dispatched_task.json",
    output_file: str = "dispatch_evaluation.json",
) -> None:
    """Evaluate dispatch results against original task assignments.

    Reads decomposed_task.json and dispatched_task.json, compares role
    assignments, validates scoring consistency, and saves evaluation to
    dispatch_evaluation.json.
    """
    # Validate input files
    decomposed_path = Path(decomposed_file)
    dispatched_path = Path(dispatched_file)
    
    if not decomposed_path.exists():
        logger.error("Decomposed file not found: %s", decomposed_file)
        print(f"\n{RED}Error: File not found: {decomposed_file}{RESET}")
        print(f"{DIM}Run 'uv run main.py decompose' first to generate tasks.{RESET}")
        return
    
    if not dispatched_path.exists():
        logger.error("Dispatched file not found: %s", dispatched_file)
        print(f"\n{RED}Error: File not found: {dispatched_file}{RESET}")
        print(f"{DIM}Run 'uv run main.py dispatch' first to generate role assignments.{RESET}")
        return

    # Load both files
    with open(decomposed_path) as f:
        decomposed_data = json.load(f)
    
    with open(dispatched_path) as f:
        dispatched_data = json.load(f)

    print(f"\n{DIM}  Loaded {decomposed_file} and {dispatched_file}{RESET}")

    # Prepare input for LLM
    evaluation_input = {
        "decomposed_tasks": decomposed_data,
        "dispatched_results": dispatched_data
    }
    
    prompt_template = load_prompt("dispatch_evaluation")

    # Replace placeholders with actual file content
    input_json = json.dumps(evaluation_input, indent=2, ensure_ascii=False)
    system_prompt = (
        prompt_template
        .replace("{input_files}", input_json)
        .replace("{ai_roles}", ", ".join(AI_ROLES))
        .replace("{human_roles}", ", ".join(HUMAN_ROLES))
        .replace("{all_roles}", ", ".join(ALL_ROLES))
    )

    print(f"{DIM}  Evaluating dispatch results...{RESET}", end="", flush=True)

    raw_response = client.chat(
        system_prompt,
        [{"role": "user", "content": f"Evaluate the dispatch results for accuracy and provide detailed analysis."}],
    )
    print("\r" + " " * 50 + "\r", end="")

    try:
        result = parse_structured_response(raw_response, DispatchEvaluationResult)
    except ValueError as e:
        logger.error("Failed to parse evaluation response: %s", e)
        print(f"\n{RED}Error: Failed to parse response{RESET}")
        print(f"{DIM}{raw_response[:500]}{RESET}")
        return

    _display_evaluation(result)

    # Save results
    with open(output_file, "w") as f:
        json.dump(result.model_dump(), f, indent=2, ensure_ascii=False)
    print(f"  {DIM}Evaluation saved to: {output_file}{RESET}\n")
