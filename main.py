"""JobCopilot CLI entry point - runs one analysis pass.

For the web backoffice run: .venv/bin/python webapp.py
"""
import sys

from src import database, runner

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python main.py <username>")
    user = database.user_by_name(sys.argv[1])
    if not user:
        sys.exit("unknown username")
    runner.run(user["id"], on_log=print)
