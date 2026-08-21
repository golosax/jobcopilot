"""JobCopilot CLI entry point - runs one analysis pass.

For the web backoffice run: .venv/bin/python webapp.py
"""
from src import runner

if __name__ == "__main__":
    runner.run(on_log=print)
