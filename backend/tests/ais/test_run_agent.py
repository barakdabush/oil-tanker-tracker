"""Unit tests for .github/agents/run_agent.py wrap-up behavior."""

import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[3] / ".github" / "agents"
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

import run_agent  # noqa: E402


def test_chat_once_omits_tools_when_empty(monkeypatch):
    captured = {}

    def fake_post(url, headers, payload):
        captured["body"] = json.loads(payload.decode("utf-8"))
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(run_agent, "_post_json", fake_post)
    run_agent._chat_once("https://example.com", "key", "model", [{"role": "user", "content": "hi"}], [])
    assert "tools" not in captured["body"]


def test_main_runs_wrapup_turn_when_max_turns_exhausted(tmp_path, monkeypatch):
    prompt_file = tmp_path / "prompt.md"
    output_file = tmp_path / "summary.md"
    stats_file = tmp_path / "stats.json"
    prompt_file.write_text("Investigate incident", encoding="utf-8")

    calls = []

    def fake_chat(providers, messages, tools):
        calls.append(len(tools))
        if tools:
            return (
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": f"tc_{len(calls)}",
                                        "type": "function",
                                        "function": {"name": "list_dir", "arguments": '{"path": "."}'},
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
                "test-model",
            )
        return (
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "### Summary\nInvestigated.\n\nTRIAGE_RESULT: {\"severity\": \"P2\", \"fixable\": false}",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
            },
            "test-model",
        )

    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(run_agent, "_chat", fake_chat)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_agent.py",
            "--mode",
            "fix",
            "--max-turns",
            "2",
            "--prompt-file",
            str(prompt_file),
            "--output",
            str(output_file),
            "--stats-output",
            str(stats_file),
        ],
    )

    run_agent.main()

    assert calls == [7, 7, 0]
    out = output_file.read_text(encoding="utf-8")
    assert "### Summary" in out
    assert "TRIAGE_RESULT:" in out
    stats = json.loads(stats_file.read_text(encoding="utf-8"))
    assert stats["total_tokens"] == 65
