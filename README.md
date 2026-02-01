# ScrumAI

AI-powered Scrum Master and Product Owner assistant that decomposes high-level goals into executable sub-tasks, more project details on PRD.md.

## Prerequisites

- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) (recommended for dependency management)

## Setup

1.  **Clone the repository:**
    ```bash
    git clone <repository_url>
    cd ScrumAI
    ```

2.  **Install dependencies:**
    Use `uv` to sync dependencies from `pyproject.toml`.
    ```bash
    uv sync
    ```

## Configuration

This project uses Google's Gemini models. You need an API key to run it.

1.  **Get a Google Gemini API Key:**
    - Visit [Google AI Studio](https://aistudio.google.com/).
    - Sign in with your Google account.
    - Click on **"Get API key"** (or "Create API key").
    - Copy your key.

2.  **Configure environment variables:**
    - Copy the example environment file:
      ```bash
      cp .env.example .env
      ```
    - Open `.env` in your text editor and paste your API key:
      ```ini
      GEMINI_API_KEY=your_actual_api_key_here
      GEMINI_MODEL=gemini-2.5-flash
      ```
    - You can also change the `GEMINI_MODEL` if you want to use a different model version.

## Usage

Run the main script using `uv run` to ensure it uses the correct virtual environment.

### Decompose a Task
You can provide a task description directly via the command line:

```bash
uv run python main.py -t "Develop a user registration flow using React and Firebase"
```

### Decompose from a File
Alternatively, you can provide a markdown file containing the goal:

```bash
uv run python main.py -f goal.md
```

### Output
The script will generate a JSON file (default: `decomposed_task.json`) containing the broken-down stories and tasks.

## Help
To see all available options:
```bash
uv run python main.py --help
```
# ScrumAI
