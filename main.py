
from google import genai
import sys
import os
import json
import re
import argparse
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    logger.error("GEMINI_API_KEY not found in environment variables. Please set it in .env file.")
    sys.exit(1)

client = genai.Client(api_key=api_key)
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp")


TASK_DECOMPOSITION_PROMPT = """
You are an expert Scrum Master and Product Owner with deep experience in agile software development.

## Your Role
You specialize in breaking down high-level goals into executable, well-structured sub-tasks that can be managed on a Kanban board.

## Instructions
Given a high-level goal from the user, perform the following:

### Step 1: Chain of Thought Analysis
Think through the goal systematically:
- What is the core objective?
- What are the key components/features needed?
- What are the technical requirements?
- What dependencies exist between components?
- What could be done in parallel vs. sequentially?

### Step 2: Decompose into Sub-task Tree
Break down the goal into a hierarchical structure of tasks:
- **Epic**: The high-level goal (what the user provided)
- **Stories**: Major deliverables (3-7 stories typically)
- **Tasks**: Specific, actionable work items (2-5 per story)

### Step 3: For Each Task, Define
- `task_id`: Unique identifier (uuid, e.g., "TASK-001")
- `title`: Clear, action-oriented title
- `description`: What needs to be done
- `status`: Task status (enum: "todo", "in_progress", "blocked", "done")
- `role`: Role type (enum: "Junior Developer", "Senior Developer", "Product Owner", "Scrum Master")
- `owner_type`: Either "human" or "ai"
- `assignee`: Person ID or agent ID (leave empty if unassigned)
- `estimate_hours`: Time estimate in hours (float, optional, 1-8 hours per task)
- `story_points`: Story point estimate (int, optional, e.g., 1, 2, 3, 5, 8)
- `dependencies`: List of task_ids this depends on (empty if none)
- `acceptance_criteria`: Clear criteria for task completion (markdown/text)
- `blocker_reason`: Reason for blocking (text, optional, only if status is "blocked")
- `artifacts`: Links to related docs, code diffs, screenshots (list, optional)

## Output Format
Return your response as valid JSON with this structure:

```json
{{
  "epic": {{
    "title": "High-level goal title",
    "description": "Brief description of the overall objective"
  }},
  "reasoning": "Your chain of thought analysis explaining the decomposition approach",
  "stories": [
    {{
      "id": "STORY-001",
      "title": "Story title",
      "tasks": [
        {{
          "task_id": "TASK-001",
          "title": "Task title",
          "description": "What needs to be done",
          "status": "todo",
          "role": "Junior Developer",
          "owner_type": "ai",
          "assignee": "",
          "estimate_hours": 2.0,
          "story_points": 2,
          "dependencies": [],
          "acceptance_criteria": "Clear criteria for completion",
          "blocker_reason": null,
          "artifacts": [],
        }}
      ]
    }}
  ],
  "execution_plan": {{
    "phases": [
      {{
        "phase": 1,
        "parallel_tasks": ["TASK-001", "TASK-002"],
        "description": "Initial setup can be done in parallel"
      }}
    ],
    "total_estimated_hours": 16,
    "critical_path": ["TASK-001", "TASK-003", "TASK-005"]
  }}
}}
```

## Guidelines
- Keep tasks small and focused (ideally completable in 1-4 hours)
- Identify tasks that can run in parallel to maximize efficiency
- Flag tasks that need human decision-making with `requires_review: true`
- Assign simpler tasks to "Junior Developer", complex/critical ones to "Senior Developer"
- Mark tasks requiring human expertise or decisions as "Human Required"
- Always provide clear acceptance criteria in descriptions

---

## User's High-Level Goal:
{task_description}

Now decompose this goal into an executable sub-task tree.
"""


def decompose_task(task_description: str) -> str:
    """Decompose a high-level goal into executable sub-tasks."""
    prompt = TASK_DECOMPOSITION_PROMPT.format(task_description=task_description)
    
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt
        )
        
        logger.info("Message from LLM: %s", response.text)
        return response.text
    except Exception as e:
        logger.error(f"Error calling Gemini API: {e}")
        return ""


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Decompose high-level goals into executable sub-tasks"
    )
    parser.add_argument(
        "-f", "--file",
        type=str,
        help="Path to a markdown file containing the high-level goal"
    )
    parser.add_argument(
        "-t", "--task",
        type=str,
        help="High-level goal as a text string"
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="decomposed_task.json",
        help="Output JSON file path (default: decomposed_task.json)"
    )
    return parser.parse_args()


def get_task_input(args: argparse.Namespace) -> str | None:
    """Get task description from file, argument, or use default."""
    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            logger.error(f"File not found: {args.file}")
            return None
        logger.info(f"Reading goal from: {args.file}")
        return file_path.read_text(encoding="utf-8")
    
    if args.task:
        return args.task
    
    # Default example
    logger.info("No input provided. Using example task.")
    logger.info("Usage: python main.py -f goal.md")
    logger.info("       python main.py -t \"Your task description\"")
    return "Develop a login page with email/password authentication"


def extract_json(response: str) -> dict | None:
    """Extract JSON from LLM response (handles code blocks or raw JSON)."""
    # Try to extract from ```json code block
    json_match = re.search(r'```json\s*([\s\S]*?)```', response)
    json_str = json_match.group(1) if json_match else response
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning(f"Could not parse JSON: {e}")
        return None


def save_result(data: dict, output_path: str) -> None:
    """Save decomposed task data to JSON file."""
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Saved to {output_path}")


def main():
    args = parse_arguments()
    
    # Get task input
    task = get_task_input(args)
    if task is None:
        return
    
    # Show task preview
    preview = task[:200] + "..." if len(task) > 200 else task
    logger.info(f"Decomposing task: {preview}")
    
    # Decompose and display
    result = decompose_task(task)
    logger.info(f"LLM Response:\n{result}")
    
    # Parse and save
    parsed = extract_json(result)
    if parsed:
        save_result(parsed, args.output)


if __name__ == "__main__":
    main()
