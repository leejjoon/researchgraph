"""Real authenticated ASGI HTTP calls, adapted to shared CLI-shaped assertions.

Exit codes here are assertion conveniences, not evidence of an installed CLI.
The response/status remain available so authority failures cannot masquerade as
ordinary graph RuleViolations.
"""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from researchgraph.model import RuleViolation, format_timestamp
from researchgraph.registry import OPERATIONS
from tests.helpers import NOW

_BY_NAME = {operation["name"]: operation for operation in OPERATIONS}


def _wire(value):
    if isinstance(value, datetime):
        return format_timestamp(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    return value


def call_http(root, name, arguments, *, actor, role=None, read_only=False):
    """Own a real TestClient lifespan, launch-token exchange and cookie jar."""
    from starlette.testclient import TestClient

    from researchgraph.serve import create_app
    from tests.test_cli import prepare_console

    origin = "http://127.0.0.1:8765"
    prepare_console(["--project", str(root)])
    app = create_app(root, actor=actor, read_only=read_only, origin=origin)
    payload = _wire(arguments)
    if role is not None:
        payload["role"] = role
    assert not {"actor", "read_only", "project", "as_agent"} & payload.keys()
    with TestClient(app, base_url=origin) as client:
        exchange = client.post(
            "/api/session",
            json={"token": app.state.launch_token},
            headers={"Origin": origin},
        )
        assert exchange.status_code == 200, exchange.text
        if _BY_NAME[name]["kind"] == "write":
            response = client.post(
                f"/api/{name}", json=payload, headers={"Origin": origin}
            )
        else:
            response = client.get(
                f"/api/{name}",
                params={
                    key: json.dumps(value, ensure_ascii=False)
                    for key, value in payload.items()
                },
                headers={"Origin": origin},
            )
    return response


def operation_value(response):
    """Only graph rejections become RuleViolation; HTTP authority stays visible."""
    assert response.status_code in (200, 422), (response.status_code, response.text)
    value = response.json()
    if response.status_code == 422:
        raise RuleViolation(value["rule"], value["message"], value["ids"])
    return value


def invoke_http(root, *arguments, agent=None, role=None, now=NOW, read_only=False):
    """Parse existing argv once, then send logical read/write values over HTTP."""
    from researchgraph.cli import _actor
    from tests.door_helpers import cli_arguments

    name, payload, agent, role, read_only = cli_arguments(
        arguments, agent=agent, role=role, now=now, read_only=read_only
    )
    response = call_http(
        root, name, payload, actor=_actor(root, agent), role=role, read_only=read_only
    )
    # Never normalize 403/404/405/500 into a graph rejection.
    assert response.status_code in (200, 422, 503), (
        response.status_code,
        response.text,
    )
    value = response.json()
    failed = response.status_code != 200
    if failed:
        assert set(value) == {"rule", "message", "ids"}
        assert isinstance(value["rule"], str)
        assert isinstance(value["message"], str)
        assert isinstance(value["ids"], list)
    text = json.dumps(value, ensure_ascii=False)
    return SimpleNamespace(
        exit_code=3 if response.status_code == 503 else 2 if failed else 0,
        stdout="" if failed else text,
        stderr=text if failed else "",
        output=text,
        status_code=response.status_code,
        native_result=response,
    )
