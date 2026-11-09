"""LLM access via the Claude Code CLI or the OpenAI Codex CLI, both authenticated
through the user's existing subscription (no API key, no per-token billing).

complete()/complete_json() dispatch on `backend` ("claude" default, or "codex" -
see llm_codex.py for the Codex path and its ChatGPT-auth guard). Claude shells out
to `claude -p ... --output-format json` and reads the `result` field, using the
logged-in Claude Code session (Pro/Max subscription).
"""
import json
import os
import re
import subprocess
import tempfile

from . import llm_codex

_CLI = os.environ.get("CLAUDE_CLI", "claude")
_TIMEOUT = 240


def complete(prompt, model=None, max_tokens=None, system=None, backend="claude", effort=None, isolated=False, timeout=None):
    """Send a single-turn prompt and return the text response, via the chosen backend.

    effort / isolated / timeout only apply to the claude backend. isolated runs the
    CLI with no tools, no user/project settings and from an empty temp directory, so
    CLAUDE.md files and auto-memory can't leak into the prompt.
    """
    if backend == "codex":
        return llm_codex.complete(prompt, model=model, max_tokens=max_tokens, system=system)
    return _complete_claude(
        prompt, model=model, max_tokens=max_tokens, system=system, effort=effort, isolated=isolated, timeout=timeout
    )


def _complete_claude(prompt, model=None, max_tokens=None, system=None, effort=None, isolated=False, timeout=None):
    cmd = [_CLI, "-p", prompt, "--output-format", "json"]
    if system:
        cmd += ["--system-prompt", system]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    if isolated:
        cmd += ["--tools", "", "--setting-sources", ""]
    timeout = timeout or _TIMEOUT

    try:
        with tempfile.TemporaryDirectory() as empty_dir:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=empty_dir if isolated else None,
            )
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude CLI timed out after {}s".format(timeout))

    if proc.returncode != 0:
        raise RuntimeError(
            "claude CLI failed (exit {}): {}".format(proc.returncode, (proc.stderr or "").strip())
        )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("unexpected claude CLI output: {}".format(proc.stdout[:500]))

    if envelope.get("is_error"):
        raise RuntimeError("claude CLI returned an error: {}".format(envelope.get("result", "")))

    return (envelope.get("result") or "").strip()


def complete_json(prompt, model=None, max_tokens=None, system=None, backend="claude", **options):
    """Send a prompt expecting a JSON object back and parse it."""
    text = complete(prompt, model=model, max_tokens=max_tokens, system=system, backend=backend, **options)
    return _parse_json(text)


def _parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
