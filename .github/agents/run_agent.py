#!/usr/bin/env python3
"""Lightweight OpenAI-compatible tool-calling agent for incident triage & fixes.

Supports OpenRouter (Qwen, DeepSeek, Grok, etc.), Groq, or Gemini via env vars:
  - OPENROUTER_API_KEY -> https://openrouter.ai/api/v1/chat/completions
  - GROQ_API_KEY       -> https://api.groq.com/openai/v1/chat/completions
  - GEMINI_API_KEY     -> https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
Override endpoint/model anytime with AGENT_BASE_URL and AGENT_MODEL.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path.cwd().resolve()
MAX_FILE_CHARS = 24000
MAX_CMD_CHARS = 12000


def _resolve_provider() -> tuple[str, str, str]:
    explicit_url = os.getenv("AGENT_BASE_URL", "").strip()
    explicit_model = os.getenv("AGENT_MODEL", "").strip()

    if os.getenv("OPENROUTER_API_KEY", "").strip():
        return (
            explicit_url or "https://openrouter.ai/api/v1/chat/completions",
            os.getenv("OPENROUTER_API_KEY", "").strip(),
            explicit_model or "openrouter/free",
        )
    if os.getenv("GROQ_API_KEY", "").strip():
        return (
            explicit_url or "https://api.groq.com/openai/v1/chat/completions",
            os.getenv("GROQ_API_KEY", "").strip(),
            explicit_model or "llama-3.3-70b-versatile",
        )
    if os.getenv("GEMINI_API_KEY", "").strip():
        return (
            explicit_url or "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            os.getenv("GEMINI_API_KEY", "").strip(),
            explicit_model or "gemini-3.8-flash",
        )
    raise RuntimeError("Set one of OPENROUTER_API_KEY, GROQ_API_KEY, or GEMINI_API_KEY.")


def _safe_path(rel_path: str) -> Path:
    p = (REPO_ROOT / rel_path).resolve()
    if p != REPO_ROOT and REPO_ROOT not in p.parents:
        raise ValueError(f"Path escapes repo root: {rel_path}")
    return p


def _assert_writable(rel_path: str) -> Path:
    p = _safe_path(rel_path)
    norm = p.relative_to(REPO_ROOT).as_posix()
    if not (norm.startswith("backend/") or norm.startswith("frontend/")):
        raise ValueError(f"Write outside backend/ or frontend/ is forbidden: {norm}")
    name = p.name
    if re.match(r"^(Dockerfile|docker-compose|\.env)", name, re.I):
        raise ValueError(f"Write to protected file is forbidden: {norm}")
    return p


def tool_list_dir(path: str = ".") -> str:
    p = _safe_path(path)
    if not p.is_dir():
        return f"Not a directory: {path}"
    items = []
    for child in sorted(p.iterdir()):
        if child.name in {".git", "node_modules", "__pycache__", ".next", ".venv"}:
            continue
        suffix = "/" if child.is_dir() else ""
        items.append(child.name + suffix)
    return "\n".join(items[:200]) or "(empty)"


def tool_glob_files(pattern: str) -> str:
    matches = []
    for p in sorted(REPO_ROOT.glob(pattern)):
        rel = p.relative_to(REPO_ROOT).as_posix()
        if any(seg in rel.split("/") for seg in (".git", "node_modules", "__pycache__", ".next")):
            continue
        matches.append(rel)
        if len(matches) >= 150:
            break
    return "\n".join(matches) or "No matches."


def tool_grep_search(pattern: str, path: str = ".") -> str:
    target = _safe_path(path)
    res = subprocess.run(
        ["grep", "-rnE", "--exclude-dir=.git", "--exclude-dir=node_modules", "--exclude-dir=__pycache__", pattern, str(target)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    lines = res.stdout.splitlines()[:80]
    cleaned = [line.replace(str(REPO_ROOT) + "/", "") for line in lines]
    return "\n".join(cleaned) or "No matches."


def tool_read_file(path: str, start_line: int = 1, end_line: int = 300) -> str:
    p = _safe_path(path)
    if not p.is_file():
        return f"File not found: {path}"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start_line)
    end = min(len(lines), max(start, end_line))
    out = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1))
    return out[:MAX_FILE_CHARS]


def tool_write_file(path: str, content: str) -> str:
    p = _assert_writable(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} chars to {path}"


def tool_replace_in_file(path: str, old_text: str, new_text: str) -> str:
    p = _assert_writable(path)
    if not p.is_file():
        return f"File not found: {path}"
    text = p.read_text(encoding="utf-8")
    count = text.count(old_text)
    if count == 0:
        return "old_text not found in file."
    if count > 1:
        return f"old_text matched {count} times; provide more surrounding context."
    p.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
    return f"Updated {path}"


def tool_run_tests(suite: str = "backend") -> str:
    if suite == "backend":
        cmd = [sys.executable, "-m", "pytest", "-c", "backend/pytest.ini", "backend/tests/ais", "-q"]
        env = {**os.environ, "PYTHONPATH": "backend"}
    elif suite == "frontend":
        cmd = ["frontend/node_modules/.bin/tsc", "--noEmit", "-p", "frontend"]
        env = os.environ
    else:
        return "Unknown suite. Use 'backend' or 'frontend'."
    res = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
    combined = f"exit={res.returncode}\n{res.stdout}\n{res.stderr}".strip()
    return combined[-MAX_CMD_CHARS:]


READ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories under a relative path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_files",
            "description": "Find files matching a glob pattern (e.g. 'backend/app/**/*.py').",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "Search regex pattern in files under path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read numbered lines from a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "default": 1},
                    "end_line": {"type": "integer", "default": 300},
                },
                "required": ["path"],
            },
        },
    },
]

WRITE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file under backend/ or frontend/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Replace an exact unique substring in a file under backend/ or frontend/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run 'backend' pytest suite or 'frontend' tsc typecheck.",
            "parameters": {
                "type": "object",
                "properties": {"suite": {"type": "string", "enum": ["backend", "frontend"]}},
                "required": ["suite"],
            },
        },
    },
]


def _dispatch(name: str, args: dict, allow_write: bool) -> str:
    try:
        if name == "list_dir":
            return tool_list_dir(**args)
        if name == "glob_files":
            return tool_glob_files(**args)
        if name == "grep_search":
            return tool_grep_search(**args)
        if name == "read_file":
            return tool_read_file(**args)
        if allow_write:
            if name == "write_file":
                return tool_write_file(**args)
            if name == "replace_in_file":
                return tool_replace_in_file(**args)
            if name == "run_tests":
                return tool_run_tests(**args)
        return f"Tool not allowed: {name}"
    except Exception as e:
        return f"Tool error ({name}): {e}"


def _chat(url: str, api_key: str, model: str, messages: list, tools: list) -> dict:
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "tools": tools,
        "temperature": 0.2,
    }).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "oil-tanker-tracker-agent",
    }
    for attempt in range(5):
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f"LLM HTTP {e.code}: {body[:500]}")
        except Exception as e:
            if attempt < 4:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f"LLM request failed: {e}")
    raise RuntimeError("Exceeded LLM retries")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["triage", "fix"], required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-turns", type=int, default=None)
    args = parser.parse_args()

    url, api_key, model = _resolve_provider()
    allow_write = args.mode == "fix"
    tools = READ_TOOLS + (WRITE_TOOLS if allow_write else [])
    max_turns = args.max_turns or (35 if allow_write else 20)

    prompt_text = Path(args.prompt_file).read_text(encoding="utf-8")
    messages: list[dict] = [{"role": "user", "content": prompt_text}]
    print(f"Running {args.mode} agent with model={model} via {url}", file=sys.stderr)

    final_text = ""
    for turn in range(1, max_turns + 1):
        resp = _chat(url, api_key, model, messages, tools)
        msg = resp["choices"][0]["message"]
        messages.append(msg)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            final_text = msg.get("content") or ""
            break
        for tc in tool_calls:
            fn = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                fn_args = {}
            result = _dispatch(fn, fn_args, allow_write)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })
        print(f"Turn {turn}: executed {len(tool_calls)} tool(s)", file=sys.stderr)

    Path(args.output).write_text(final_text, encoding="utf-8")


if __name__ == "__main__":
    main()
