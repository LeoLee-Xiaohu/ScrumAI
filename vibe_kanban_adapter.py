import sqlite3
import json
import uuid
import os
import argparse
from datetime import datetime

# Default vibe-kanban db paths based on OS
DEFAULT_MAC_DB = os.path.expanduser("~/Library/Application Support/ai.bloop.vibe-kanban/db.v2.sqlite")
DEFAULT_LINUX_DB = os.path.expanduser("~/.local/share/vibe-kanban/db.v2.sqlite")

def get_default_db_path():
    if os.path.exists(DEFAULT_MAC_DB):
        return DEFAULT_MAC_DB
    if os.path.exists(DEFAULT_LINUX_DB):
        return DEFAULT_LINUX_DB
    if os.path.exists(os.path.expanduser("~/Library/Application Support/vibe-kanban/db.v2.sqlite")):
        return os.path.expanduser("~/Library/Application Support/vibe-kanban/db.v2.sqlite")
    # For local dev of vibe-kanban, it might be in dev_assets
    return DEFAULT_MAC_DB # Fallback

def parse_args():
    parser = argparse.ArgumentParser(description="Inject ScrumAI tasks into Vibe Kanban SQLite DB")
    parser.add_argument("--decomposed", default="decomposed_task.json", help="Path to decomposed_task.json")
    parser.add_argument("--evaluation", default="dispatch_evaluation.json", help="Path to dispatch_evaluation.json")
    parser.add_argument("--db", default=get_default_db_path(), help="Path to vibe-kanban db.v2.sqlite")
    parser.add_argument("--project-name", default="ScrumAI Project", help="Name of the vibe-kanban project to create/use")
    return parser.parse_args()

def ensure_project(cursor, project_name):
    # Get project if it exists
    cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
    project_row = cursor.fetchone()
    if project_row:
        project_id = project_row[0]
    else:
        project_id = uuid.uuid4().bytes
        now = datetime.utcnow().isoformat()
        cursor.execute(
            "INSERT INTO projects (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (project_id, project_name, now, now)
        )
        
    return project_id

def ensure_repo_link(cursor, project_id):
    # 1. Ensure the repo exists in the 'repos' table based on current working directory
    current_path = os.path.abspath(os.getcwd())
    repo_name = os.path.basename(current_path)
    
    cursor.execute("SELECT id FROM repos WHERE path = ?", (current_path,))
    repo_row = cursor.fetchone()
    
    if repo_row:
        repo_id = repo_row[0]
    else:
        repo_id = uuid.uuid4().bytes
        now = datetime.utcnow().isoformat()
        cursor.execute(
            "INSERT INTO repos (id, path, name, display_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (repo_id, current_path, repo_name, repo_name, now, now)
        )
        
    # 2. Link the repository to the project
    cursor.execute("SELECT id FROM project_repos WHERE project_id = ? AND repo_id = ?", (project_id, repo_id))
    if not cursor.fetchone():
        link_id = uuid.uuid4().bytes
        cursor.execute(
            "INSERT INTO project_repos (id, project_id, repo_id) VALUES (?, ?, ?)",
            (link_id, project_id, repo_id)
        )

def format_description(task, evaluation=None):
    desc = f"**Role:** {task.get('role', 'Unassigned')}\n"
    desc += f"**Estimate:** {task.get('estimate_hours', 0)} hours\n"
    desc += f"**Acceptance Criteria:**\n{task.get('acceptance_criteria', 'None')}\n\n"
    
    if evaluation:
        desc += "---\n**Dispatch Evaluation Alerts:**\n"
        desc += f"Suggested Role: {evaluation.get('suggested_role', 'N/A')}\n"
        desc += f"Reason: {evaluation.get('reason', 'N/A')}\n\n"
        
    desc += "---\n**Task Description:**\n"
    desc += task.get('description', '')
    return desc

def run_export(args):
    if not os.path.exists(args.decomposed):
        print(f"Error: {args.decomposed} not found.")
        return
        
    with open(args.decomposed, 'r') as f:
        decomposed_data = json.load(f)
        
    evaluations = {}
    if os.path.exists(args.evaluation):
        with open(args.evaluation, 'r') as f:
            eval_data = json.load(f)
            # Make a map for quick lookup by task_id
            for issue in eval_data.get('role_analysis', {}).get('notable_issues', []):
                evaluations[issue['task_id']] = issue
            for issue in eval_data.get('owner_type_analysis', {}).get('false_ai_assignments', []):
                if issue['task_id'] not in evaluations:
                    evaluations[issue['task_id']] = issue
                else:
                    evaluations[issue['task_id']].update(issue)

    conn = sqlite3.connect(args.db)
    cursor = conn.cursor()
    
    try:
        project_id = ensure_project(cursor, args.project_name)
        ensure_repo_link(cursor, project_id)
        
        tasks_inserted = 0
        now = datetime.utcnow().isoformat()
        
        for story in decomposed_data.get('stories', []):
            story_title = story.get('title', 'Unknown Story')
            story_id = story.get('id', '')
            
            for task in story.get('tasks', []):
                task_id_str = task.get('task_id', '')
                title = f"[{story_id}] {task.get('title', 'Unnamed Task')}"
                
                evaluation = evaluations.get(task_id_str)
                description = format_description(task, evaluation)
                
                # Check if task already exists (by title to prevent duplicates)
                cursor.execute("SELECT id FROM tasks WHERE project_id = ? AND title = ?", (project_id, title))
                if cursor.fetchone():
                    print(f"Task already exists: {title}")
                    continue
                
                task_uuid = uuid.uuid4().bytes
                cursor.execute(
                    "INSERT INTO tasks (id, project_id, title, description, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'todo', ?, ?)",
                    (task_uuid, project_id, title, description, now, now)
                )
                tasks_inserted += 1
                print(f"Inserted: {title}")
                
        conn.commit()
        print(f"\\nSuccessfully inserted {tasks_inserted} tasks into Vibe Kanban database.")
        
    except Exception as e:
        conn.rollback()
        print(f"Database error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_export(parse_args())
