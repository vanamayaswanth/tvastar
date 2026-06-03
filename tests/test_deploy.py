"""Deploy adapter tests — exercised offline with the mock model."""

import json

from tvastar import create_agent, default_toolset
from tvastar.deploy import lambda_handler, run_github_action, serverless_handler
from tvastar.model import MockModel


def _agent():
    return create_agent(
        "deployable",
        model=MockModel(["Hello from a deployed agent."]),
        tools=default_toolset(),
    )


def test_serverless_handler():
    handler = serverless_handler(_agent())
    out = handler({"prompt": "hi"})
    assert out["text"] == "Hello from a deployed agent."
    assert out["stopped"] == "end_turn"
    assert out["steps"] == 1


def test_lambda_direct_invocation():
    handler = lambda_handler(_agent())
    resp = handler({"prompt": "hi"}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "deployed agent" in body["text"]


def test_lambda_api_gateway_proxy_event():
    handler = lambda_handler(_agent())
    event = {"body": json.dumps({"prompt": "hi"})}
    resp = handler(event, None)
    assert resp["statusCode"] == 200
    assert "deployed agent" in json.loads(resp["body"])["text"]


def test_github_action_writes_output(tmp_path, monkeypatch):
    out_file = tmp_path / "gh_output"
    monkeypatch.setenv("INPUT_PROMPT", "do the thing")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    code = run_github_action(_agent())
    assert code == 0
    written = out_file.read_text(encoding="utf-8")
    assert "deployed agent" in written
    assert "TVASTAR_EOF" in written  # multiline-safe delimiter
    assert "stopped=end_turn" in written


def test_github_action_missing_prompt(monkeypatch):
    monkeypatch.delenv("INPUT_PROMPT", raising=False)
    monkeypatch.delenv("TVASTAR_PROMPT", raising=False)
    assert run_github_action(_agent()) == 2
