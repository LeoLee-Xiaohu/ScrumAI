"""ScrumAI Prompt Playground - CLI for debugging and testing prompts.

This playground mirrors the prompt structures used in scrumai-forge (Atlassian Forge app)
so prompt engineers can iterate on prompts locally without deploying to Jira.

Prompt sources from scrumai-forge:
- Brainstorm: src/lib/brainstorm-prompts.ts (4-phase Socratic dialogue)
- Issue Scoring: src/lib/issue-scorer.ts (5-dimension readiness scoring)
- Task Decomposition: original main.py (goal → sub-task tree)

Usage:
    # Interactive brainstorm session
    python main.py brainstorm

    # Brainstorm with ticket context from file
    python main.py brainstorm -f ticket.md

    # Score an issue for AI-readiness
    python main.py score -f ticket.md
    python main.py score -t "Build a login page with email/password auth"

    # Decompose a goal into sub-tasks
    python main.py decompose -f goal.md
    python main.py decompose -t "Implement user authentication system"

    # Specify LLM provider
    python main.py --provider openai brainstorm
    python main.py --provider gemini decompose -f goal.md
"""

import argparse
import logging
import sys
from pathlib import Path

from client import get_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _read_input(args: argparse.Namespace) -> str | None:
    """Read input from file or text argument."""
    if args.file:
        path = Path(args.file)
        if not path.exists():
            logger.error("File not found: %s", args.file)
            return None
        return path.read_text(encoding="utf-8")

    if args.task:
        return args.task

    return None


def cmd_brainstorm(args: argparse.Namespace) -> None:
    """Run the brainstorm command."""
    from runners.brainstorm import run_brainstorm

    client = get_client(args.provider)
    context = _read_input(args)
    run_brainstorm(client, context)


def cmd_score(args: argparse.Namespace) -> None:
    """Run the score command."""
    from runners.scoring import run_scoring

    client = get_client(args.provider)
    text = _read_input(args)
    if not text:
        logger.error("Please provide issue text via -f or -t")
        sys.exit(1)
    run_scoring(client, text)


def cmd_decompose(args: argparse.Namespace) -> None:
    """Run the decompose command."""
    from runners.task import run_decomposition

    client = get_client(args.provider)
    text = _read_input(args)
    if not text:
        # Use default example
        text = "Develop a login page with email/password authentication"
        logger.info("No input provided, using example: %s", text)
    run_decomposition(client, text, output=args.output)


def cmd_list_prompts(_args: argparse.Namespace) -> None:
    """List all available prompts."""
    prompts_dir = Path(__file__).parent / "prompts"
    print("\nAvailable prompts:")
    print("=" * 40)
    for prompt_file in sorted(prompts_dir.glob("*.md")):
        name = prompt_file.stem
        # Read first line as description
        first_line = prompt_file.read_text(encoding="utf-8").split("\n")[0].strip()
        print(f"  {name:<25} {first_line[:50]}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ScrumAI Prompt Playground - Debug and test AI prompts locally",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py brainstorm                    Interactive brainstorm
  python main.py brainstorm -f ticket.md       Brainstorm with ticket context
  python main.py score -f ticket.md            Score issue readiness
  python main.py decompose -t "Build a REST API"  Decompose a goal
  python main.py prompts                       List available prompts
        """,
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "gemini"],
        default=None,
        help="LLM provider (default: auto-detect from env vars)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # brainstorm
    p_brainstorm = subparsers.add_parser(
        "brainstorm", help="Interactive 4-phase brainstorm session"
    )
    p_brainstorm.add_argument("-f", "--file", help="File with ticket context")
    p_brainstorm.add_argument("-t", "--task", help="Ticket context as text")
    p_brainstorm.set_defaults(func=cmd_brainstorm)

    # score
    p_score = subparsers.add_parser(
        "score", help="Score issue readiness (0-10)"
    )
    p_score.add_argument("-f", "--file", help="File with issue text")
    p_score.add_argument("-t", "--task", help="Issue text")
    p_score.set_defaults(func=cmd_score)

    # decompose
    p_decompose = subparsers.add_parser(
        "decompose", help="Decompose goal into sub-tasks"
    )
    p_decompose.add_argument("-f", "--file", help="File with goal description")
    p_decompose.add_argument("-t", "--task", help="Goal description as text")
    p_decompose.add_argument(
        "-o", "--output", default="decomposed_task.json", help="Output JSON file"
    )
    p_decompose.set_defaults(func=cmd_decompose)

    # prompts
    p_prompts = subparsers.add_parser("prompts", help="List available prompts")
    p_prompts.set_defaults(func=cmd_list_prompts)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
