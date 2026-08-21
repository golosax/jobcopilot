"""LLM access via the OpenAI Codex CLI (uses a ChatGPT Plus/Pro/Business/Edu/
Enterprise plan, no API key).

Shells out to `codex exec ... --output-last-message <file>` and reads that file.
Codex CLI has a known failure mode where "Sign in with ChatGPT" can silently fall
back to an auto-generated, billed API key (see openai/codex#2000, #2733), which
would defeat the whole point of using the subscription. To guard against that,
every call first runs `codex login status` and refuses to proceed unless it
reports ChatGPT auth - this never falls back to per-token billing silently.
"""
import os
import subprocess
import tempfile

_CLI = os.environ.get("CODEX_CLI", "codex")
_TIMEOUT = 240
_CHATGPT_STATUS = "Logged in using ChatGPT"


def _require_chatgpt_auth():
    try:
        proc = subprocess.run(
            [_CLI, "login", "status"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        raise RuntimeError("codex CLI not found - install it first (brew install --cask codex)")
    except subprocess.TimeoutExpired:
        raise RuntimeError("codex login status timed out")

    # the CLI prints the status line to stdout when authenticated and to stderr
    # when not - check both so a stream choice we didn't predict can't defeat this
    status = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if status != _CHATGPT_STATUS:
        raise RuntimeError(
            "codex is not signed in with ChatGPT (status: '{}') - refusing to run "
            "to avoid per-token API billing. Run `codex login` and sign in with "
            "your ChatGPT account (not an API key).".format(status or "unknown")
        )


def complete(prompt, model=None, max_tokens=None, system=None):
    """Send a single-turn prompt and return the text response."""
    _require_chatgpt_auth()

    full_prompt = "{}\n\n{}".format(system, prompt) if system else prompt

    fd, out_path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)

    cmd = [
        _CLI, "exec", full_prompt,
        "--skip-git-repo-check",
        "--sandbox", "read-only",
        "--ephemeral",
        "--output-last-message", out_path,
    ]
    if model:
        cmd += ["-m", model]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError("codex CLI timed out after {}s".format(_TIMEOUT))

    if proc.returncode != 0:
        if os.path.exists(out_path):
            os.remove(out_path)
        raise RuntimeError(
            "codex CLI failed (exit {}): {}".format(proc.returncode, (proc.stderr or "").strip())
        )

    try:
        with open(out_path, "r", encoding="utf-8") as f:
            result = f.read().strip()
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass

    return result
