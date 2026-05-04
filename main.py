"""ScrumAI Prompt Playground - CLI for debugging and testing prompts.

This playground mirrors the prompt structures used in scrumai-forge (Atlassian Forge app)
so prompt engineers can iterate on prompts locally without deploying to Jira.

Prompt sources from scrumai-forge:
- Brainstorm: src/lib/brainstorm-prompts.ts (4-phase Socratic dialogue)
- Issue Scoring: src/lib/issue-scorer.ts (5-dimension readiness scoring)
- Task Decomposition: original main.py (goal → sub-task tree)

Usage:
    # Interactive brainstorm session
    uv run main.py brainstorm

    # Brainstorm with ticket context from file
    uv run main.py brainstorm -f ticket.md

    # Score an issue for AI-readiness
    uv run main.py score -f ticket.md
    uv run main.py score -t "Build a login page with email/password auth"

    # Decompose a goal into sub-tasks
    uv run main.py decompose -f goal.md
    uv run main.py decompose -t "Implement user authentication system"

    # Specify LLM provider
    uv run main.py --provider openai brainstorm
    uv run main.py --provider minimax decompose -f goal.md
    uv run main.py --provider gemini decompose -f goal.md

    # Integration with Vibe Kanban
    uv run main.py export-kanban -d my_dispatch.json --project-name "Your Project Name"
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
        text = "Develop a login page with email/password authentication"
        logger.info("No input provided, using example: %s", text)
    run_decomposition(
        client,
        text,
        output=args.output,
        repo_url=args.repo_url,
        branch=args.branch,
        focus_paths=getattr(args, "focus_paths", None),
    )


def cmd_dispatch(args: argparse.Namespace) -> None:
    """Run the dispatch command."""
    from runners.dispatch import run_dispatch

    client = get_client(args.provider)
    run_dispatch(client, input_file=args.file, output_file=args.output)


def cmd_evaluate_dispatch(args: argparse.Namespace) -> None:
    """Run the dispatch evaluation command."""
    from runners.dispatch_evaluation import run_evaluation

    client = get_client(args.provider)
    run_evaluation(
        client,
        decomposed_file=args.decomposed,
        dispatched_file=args.dispatched,
        output_file=args.output,
    )


def cmd_export_kanban(args: argparse.Namespace) -> None:
    """Run the export-kanban command."""
    from mcp_adapter import run_mcp_export
    success = run_mcp_export(args)
    if not success:
        print("Export failed.")
        sys.exit(1)


def cmd_clear_kanban(args: argparse.Namespace) -> None:
    """Run the clear-kanban command."""
    from mcp_adapter import run_mcp_clear

    success = run_mcp_clear(args)
    if not success:
        print("Clear failed.")
        sys.exit(1)


def cmd_list_kanban_projects(_args: argparse.Namespace) -> None:
    """List Vibe Kanban organizations and projects with IDs."""
    from mcp_adapter import McpClient

    print("Starting Vibe Kanban MCP Server...")
    client = McpClient()
    try:
        organizations = client.list_organizations()
        if not organizations:
            print("No organizations found. Make sure you are signed in to Vibe Kanban.")
            return
        for org in organizations:
            print(f"Organization: {org.name} (id={org.id})")
            projects = client.list_projects(org.id)
            if not projects:
                print("  No projects found.")
                continue
            for project in projects:
                print(f"  - id={project.id} name={project.name}")
    finally:
        client.close()


def cmd_list_kanban_repos(_args: argparse.Namespace) -> None:
    """List Vibe Kanban repositories with IDs."""
    from mcp_adapter import McpClient

    print("Starting Vibe Kanban MCP Server...")
    client = McpClient()
    try:
        repos = client.list_repos()
        if not repos:
            print("No repositories found.")
            return
        for repo in repos:
            default_branch = repo.get("default_target_branch") or repo.get("default_branch") or ""
            print(
                f"- id={repo.get('id')} "
                f"name={repo.get('name')} "
                f"path={repo.get('path')} "
                f"default_branch={default_branch}"
            )
    finally:
        client.close()


def cmd_list_kanban_workspaces(args: argparse.Namespace) -> None:
    """List Vibe Kanban workspaces with IDs."""
    from mcp_adapter import McpClient

    print("Starting Vibe Kanban MCP Server...")
    client = McpClient()
    try:
        workspaces = client.list_workspaces(
            archived=args.archived,
            pinned=args.pinned,
            branch=args.branch,
            name_search=args.name_search,
            limit=args.limit,
        )
        if not workspaces:
            print("No workspaces found.")
            return
        for workspace in workspaces:
            print(
                f"- id={workspace.get('id')} "
                f"name={workspace.get('name')} "
                f"branch={workspace.get('branch')} "
                f"issue_id={workspace.get('issue_id')} "
                f"archived={workspace.get('archived')}"
            )
    finally:
        client.close()


def cmd_delete_kanban_workspace(args: argparse.Namespace) -> None:
    """Delete a Vibe Kanban workspace by ID."""
    from mcp_adapter import McpClient

    if not args.yes:
        print("Refusing to delete workspace without --yes.")
        print("Re-run with --yes to confirm deletion.")
        sys.exit(1)

    print("Starting Vibe Kanban MCP Server...")
    client = McpClient()
    try:
        success = client.delete_workspace(
            workspace_id=args.workspace_id,
            delete_remote=args.delete_remote,
            delete_branches=args.delete_branches,
        )
        if not success:
            print(f"Failed to delete workspace {args.workspace_id}.")
            sys.exit(1)
        print(f"Deleted workspace {args.workspace_id}.")
    finally:
        client.close()


def cmd_delete_kanban_repo(args: argparse.Namespace) -> None:
    """Delete a Vibe Kanban repo registration by ID."""
    from mcp_adapter import McpClient

    if not args.yes:
        print("Refusing to delete repo without --yes.")
        print("Re-run with --yes to confirm deletion.")
        sys.exit(1)

    print("Starting Vibe Kanban MCP Server...")
    client = McpClient()
    try:
        repo = client.get_repo(args.repo_id)
        if not repo:
            print(f"Repo {args.repo_id} was not found.")
            sys.exit(1)

        print(
            "Deleting Vibe Kanban repo registration: "
            f"id={repo.get('id')} name={repo.get('name')} path={repo.get('path')}"
        )
        success, message = client.delete_repo(
            repo_id=args.repo_id,
            backend_url=args.backend_url,
        )
        if not success:
            print(f"Failed to delete repo {args.repo_id}.")
            if message:
                print(message)
            sys.exit(1)
        print(f"Deleted repo {args.repo_id}.")
    finally:
        client.close()


def cmd_watch_kanban(args: argparse.Namespace) -> None:
    """Run the watch-kanban command - promotes Backlog tasks whose blockers are Done."""
    from mcp_adapter import run_mcp_watch

    success = run_mcp_watch(args)
    if not success:
        print("Watch failed.")
        sys.exit(1)


def cmd_auto_workspace(args: argparse.Namespace) -> None:
    """Run the auto-workspace watcher."""
    from runners.auto_workspace import run_auto_workspace

    success = run_auto_workspace(args)
    if not success:
        print("Auto workspace watcher failed.")
        sys.exit(1)


def cmd_deploy(args: argparse.Namespace) -> None:
    """Run export-kanban then automatically start watch-kanban."""
    from mcp_adapter import run_mcp_export, run_mcp_watch

    print("=" * 50)
    print("Phase 1: Exporting tasks to Vibe Kanban")
    print("=" * 50)
    success = run_mcp_export(args)
    if not success:
        print("Export failed. Aborting deploy.")
        sys.exit(1)

    if args.no_watch:
        print("\nDeploy complete (watcher skipped via --no-watch).")
        return

    print()
    print("=" * 50)
    print("Phase 2: Starting dependency watcher")
    print("=" * 50)
    success = run_mcp_watch(args)
    if not success:
        print("Watch failed.")
        sys.exit(1)


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
  uv run main.py brainstorm                    Interactive brainstorm
  uv run main.py brainstorm -f ticket.md       Brainstorm with ticket context
  uv run main.py score -f ticket.md            Score issue readiness
  uv run main.py decompose -t "Build a REST API"  Decompose a goal
  uv run main.py decompose -t "Add OAuth" --repo-url owner/repo  With repo context
  uv run main.py decompose -t "Add feature" --repo-url owner/repo --branch develop  Specific branch
  uv run main.py decompose -t "Add feature" --repo-url owner/repo --focus-paths src tests  Focus paths
  uv run main.py dispatch                          Dispatch roles for tasks
  uv run main.py dispatch -f decomposed_task.json  Dispatch with explicit input
  uv run main.py evaluate-dispatch             Evaluate dispatch accuracy
  uv run main.py export-kanban  --project-name "Your Project Name"   
  uv run main.py export-kanban -i my_decomposed_task.json -d my_dispatch.json --project-name "Your Project Name"
  uv run main.py deploy --project-name "Your Project Name"
  uv run main.py deploy -i my_decomposed_task.json -d my_dispatch.json --project-name "Your Project Name"
  uv run main.py deploy -i my_decomposed_task.json -d my_dispatch.json --no-watch   Export only, skip watcher
  uv run main.py auto-workspace --project-name "Your Project Name" --repo-name "ScrumAI" --github-repo owner/repo
  uv run main.py list-kanban-repos             List repo IDs for --repo-id
  uv run main.py list-kanban-projects          List project IDs for --project-id
  uv run main.py delete-kanban-repo --repo-id <id> --yes
  uv run main.py delete-kanban-workspace --workspace-id <id> --yes
  uv run main.py clear-kanban --project-name "Your Project Name" --yes
  uv run main.py prompts                       List available prompts
        """,
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "minimax", "gemini"],
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
    p_decompose.add_argument(
        "--repo-url",
        help="GitHub repository URL for context (e.g., https://github.com/owner/repo or owner/repo)",
    )
    p_decompose.add_argument(
        "--branch",
        help="Branch/tag/commit to read from (default: main or repository default)",
    )
    p_decompose.add_argument(
        "--focus-paths",
        nargs="+",
        metavar="PATH",
        help="Specific repository paths to focus on (e.g., --focus-paths src tests)",
    )
    p_decompose.set_defaults(func=cmd_decompose)

    # dispatch
    p_dispatch = subparsers.add_parser(
        "dispatch", help="Dispatch roles for decomposed tasks"
    )
    p_dispatch.add_argument(
        "-f", "--file", default="decomposed_task.json",
        help="Input JSON file with decomposed tasks (default: decomposed_task.json)",
    )
    p_dispatch.add_argument(
        "-o", "--output", default="dispatched_task.json",
        help="Output JSON file (default: dispatched_task.json)",
    )
    p_dispatch.set_defaults(func=cmd_dispatch)

    # evaluate-dispatch
    p_evaluate = subparsers.add_parser(
        "evaluate-dispatch", help="Evaluate dispatch results for accuracy"
    )
    p_evaluate.add_argument(
        "-i", "--decomposed", default="decomposed_task.json",
        help="Input JSON file with original decomposed tasks (default: decomposed_task.json)",
    )
    p_evaluate.add_argument(
        "-d", "--dispatched", default="dispatched_task.json",
        help="Input JSON file with dispatch results (default: dispatched_task.json)",
    )
    p_evaluate.add_argument(
        "-o", "--output", default="dispatch_evaluation.json",
        help="Output JSON file (default: dispatch_evaluation.json)",
    )
    p_evaluate.set_defaults(func=cmd_evaluate_dispatch)

    # export-kanban
    p_export = subparsers.add_parser(
        "export-kanban", help="Export dispatched tasks to Vibe Kanban via MCP"
    )
    p_export.add_argument(
        "-i", "--decomposed", default="decomposed_task.json",
        help="Input JSON file with original decomposed tasks (default: decomposed_task.json)",
    )
    p_export.add_argument(
        "-d", "--dispatched", default="dispatched_task.json",
        help="Path to dispatched_task.json"
    )
    p_export.add_argument(
        "--project-name", default="ScrumAI Project",
        help="Name of the vibe-kanban project"
    )
    p_export.add_argument(
        "--mapping", default="kanban_mapping.json",
        help="Path to write the task_id -> issue_id mapping (consumed by watch-kanban)",
    )
    p_export.set_defaults(func=cmd_export_kanban)

    # clear-kanban
    p_clear = subparsers.add_parser(
        "clear-kanban", help="Remove ScrumAI-exported tickets from a Vibe Kanban project via MCP"
    )
    p_clear.add_argument(
        "--project-name", default="ScrumAI Project",
        help="Name of the vibe-kanban project"
    )
    p_clear.add_argument(
        "--yes",
        action="store_true",
        help="Confirm deletion of ScrumAI-exported tickets in the target project",
    )
    p_clear.set_defaults(func=cmd_clear_kanban)

    # list-kanban-projects
    p_list_projects = subparsers.add_parser(
        "list-kanban-projects",
        help="List Vibe Kanban project IDs",
    )
    p_list_projects.set_defaults(func=cmd_list_kanban_projects)

    # list-kanban-repos
    p_list_repos = subparsers.add_parser(
        "list-kanban-repos",
        help="List Vibe Kanban repository IDs for --repo-id",
    )
    p_list_repos.set_defaults(func=cmd_list_kanban_repos)

    # list-kanban-workspaces
    p_list_workspaces = subparsers.add_parser(
        "list-kanban-workspaces",
        help="List Vibe Kanban workspace IDs",
    )
    p_list_workspaces.add_argument("--branch", help="Filter by exact branch")
    p_list_workspaces.add_argument("--name-search", help="Filter by workspace name substring")
    p_list_workspaces.add_argument("--archived", action="store_true", default=None, help="Only list archived workspaces")
    p_list_workspaces.add_argument("--pinned", action="store_true", default=None, help="Only list pinned workspaces")
    p_list_workspaces.add_argument("--limit", type=int, default=100, help="Maximum workspaces to list")
    p_list_workspaces.set_defaults(func=cmd_list_kanban_workspaces)

    # delete-kanban-workspace
    p_delete_workspace = subparsers.add_parser(
        "delete-kanban-workspace",
        help="Delete a Vibe Kanban workspace by ID",
    )
    p_delete_workspace.add_argument("--workspace-id", required=True, help="Workspace UUID to delete")
    p_delete_workspace.add_argument("--delete-remote", action="store_true", help="Also delete linked remote workspace")
    p_delete_workspace.add_argument("--delete-branches", action="store_true", help="Also delete workspace branches")
    p_delete_workspace.add_argument("--yes", action="store_true", help="Confirm workspace deletion")
    p_delete_workspace.set_defaults(func=cmd_delete_kanban_workspace)

    # delete-kanban-repo
    p_delete_repo = subparsers.add_parser(
        "delete-kanban-repo",
        help="Delete a Vibe Kanban repository registration by ID",
    )
    p_delete_repo.add_argument("--repo-id", required=True, help="Repository UUID to delete")
    p_delete_repo.add_argument(
        "--backend-url",
        help="Vibe Kanban backend URL (default: VIBE_BACKEND_URL or http://127.0.0.1:63861)",
    )
    p_delete_repo.add_argument("--yes", action="store_true", help="Confirm repo deletion")
    p_delete_repo.set_defaults(func=cmd_delete_kanban_repo)

    # watch-kanban
    p_watch = subparsers.add_parser(
        "watch-kanban",
        help="Promote Backlog tasks to To-do once their blockers reach Done",
    )
    p_watch.add_argument(
        "--mapping", default="kanban_mapping.json",
        help="Path to the task_id -> issue_id mapping file written by export-kanban",
    )
    p_watch.add_argument(
        "--decomposed", default=None,
        help="Path to decomposed_task.json (defaults to the path recorded in the mapping file)",
    )
    p_watch.add_argument(
        "--interval", type=int, default=5,
        help="Poll interval in seconds (default: 5)",
    )
    p_watch.add_argument(
        "--once", action="store_true",
        help="Scan once and exit instead of looping",
    )
    p_watch.set_defaults(func=cmd_watch_kanban)

    # auto-workspace
    p_auto_workspace = subparsers.add_parser(
        "auto-workspace",
        help="Create workspaces for In progress issues and PRs when agent work completes",
    )
    p_auto_workspace.add_argument(
        "--project-name", default="ScrumAI Project",
        help="Name of the Vibe Kanban project",
    )
    p_auto_workspace.add_argument(
        "--project-id",
        help="Exact Vibe Kanban project UUID (preferred when project names collide)",
    )
    p_auto_workspace.add_argument(
        "--repo-name",
        help="Name or path fragment of the Vibe Kanban repo to use for workspaces",
    )
    p_auto_workspace.add_argument(
        "--repo-id",
        help="Exact Vibe Kanban repo UUID to use for workspaces (preferred when available)",
    )
    p_auto_workspace.add_argument(
        "--base-branch", default="main",
        help="Workspace base branch (default: main, corresponding to origin/main)",
    )
    p_auto_workspace.add_argument(
        "--executor", default="CODEX",
        help="Vibe Kanban executor/model to use (default: CODEX)",
    )
    p_auto_workspace.add_argument(
        "--codex-model", default="gpt-5.4",
        help="Codex model ID to use when --executor CODEX (default: gpt-5.4)",
    )
    p_auto_workspace.add_argument(
        "--github-repo",
        help="GitHub repo for PRs in owner/repo format (defaults to repo origin remote when inferable)",
    )
    p_auto_workspace.add_argument(
        "--review-status", default="In review",
        help="Issue status to move to after PR creation (default: In review)",
    )
    p_auto_workspace.add_argument(
        "--pr-base",
        help="GitHub PR base branch (default: same as --base-branch)",
    )
    p_auto_workspace.add_argument(
        "--pr-draft", action="store_true",
        help="Create draft PRs",
    )
    p_auto_workspace.add_argument(
        "--skip-pr", action="store_true",
        help="Only create workspaces; do not create PRs or move issues to review",
    )
    p_auto_workspace.add_argument(
        "--mapping", default="kanban_workspace_mapping.json",
        help="Path to issue -> workspace/PR mapping file",
    )
    p_auto_workspace.add_argument(
        "--interval", type=int, default=1,
        help="Poll interval in seconds (default: 1)",
    )
    p_auto_workspace.add_argument(
        "--once", action="store_true",
        help="Scan once and exit instead of looping",
    )
    p_auto_workspace.add_argument(
        "--dry-run", action="store_true",
        help="Print planned workspace/PR actions without creating them",
    )
    p_auto_workspace.add_argument(
        "--include-existing-in-progress", action="store_true",
        help="Also process issues already in In progress on watcher startup",
    )
    p_auto_workspace.add_argument(
        "--include-manual-issues", action="store_true",
        help="Deprecated: manual issues are supported by default",
    )
    p_auto_workspace.add_argument(
        "--scrumai-only", action="store_true",
        help="Only process ScrumAI-exported issues with export markers/title pattern",
    )
    p_auto_workspace.set_defaults(func=cmd_auto_workspace)

    # deploy (export-kanban + watch-kanban in one step)
    p_deploy = subparsers.add_parser(
        "deploy",
        help="Export tasks to Vibe Kanban and start the dependency watcher",
    )
    p_deploy.add_argument(
        "-i", "--decomposed", default="decomposed_task.json",
        help="Input JSON file with original decomposed tasks (default: decomposed_task.json)",
    )
    p_deploy.add_argument(
        "-d", "--dispatched", default="dispatched_task.json",
        help="Path to dispatched_task.json",
    )
    p_deploy.add_argument(
        "--project-name", default="ScrumAI Project",
        help="Name of the vibe-kanban project",
    )
    p_deploy.add_argument(
        "--mapping", default="kanban_mapping.json",
        help="Path to write/read the task_id -> issue_id mapping",
    )
    p_deploy.add_argument(
        "--interval", type=int, default=1,
        help="Watcher poll interval in seconds (default: 1)",
    )
    p_deploy.add_argument(
        "--once", action="store_true",
        help="Run watcher once and exit instead of looping",
    )
    p_deploy.add_argument(
        "--no-watch", action="store_true",
        help="Skip the watcher phase (export only)",
    )
    p_deploy.set_defaults(func=cmd_deploy)

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
