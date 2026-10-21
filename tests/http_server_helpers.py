"""Managed real loopback HTTP transport; no ASGI in-process client."""

import json
import re
import socket
import threading
from contextlib import contextmanager
from time import monotonic, sleep

from tests.helpers import ALICE


class LoopbackServer:
    def __init__(self, root, *, actor=ALICE, **options):
        import httpx
        import uvicorn

        from researchgraph.serve import _Server, create_app

        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(128)
        self.origin = f"http://127.0.0.1:{self.listener.getsockname()[1]}"
        self.errors = []
        self.app = create_app(root, actor=actor, origin=self.origin, **options)
        self.server = _Server(
            uvicorn.Config(
                self.app,
                access_log=False,
                log_level="warning",
                proxy_headers=False,
                server_header=False,
                timeout_graceful_shutdown=1,
            )
        )
        self.client = httpx.Client(base_url=self.origin, timeout=3, trust_env=False)
        self.thread = threading.Thread(target=self._run, name="http-test-owner")

    def _run(self):
        try:
            self.server.run(sockets=[self.listener])
        except BaseException as error:
            self.errors.append(error)
            raise

    def start(self):
        import httpx

        self.thread.start()
        deadline = monotonic() + 5
        while monotonic() < deadline:
            assert self.thread.is_alive(), self.errors
            if self.server.started:
                try:
                    response = self.client.get("/")
                    assert response.status_code == 200, response.text
                    return self
                except httpx.TransportError:
                    pass
            sleep(0.01)
        raise AssertionError("Loopback server did not become ready within five seconds")

    def authenticate(self):
        response = self.client.post(
            "/api/session",
            json={"token": self.app.state.launch_token},
            headers={"Origin": self.origin},
        )
        assert response.status_code == 200, response.text
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert "domain=" not in cookie
        assert self.client.cookies
        return response

    def close(self):
        self.server.should_exit = True
        if self.thread.ident is not None:
            self.thread.join(timeout=5)
        self.client.close()
        self.listener.close()
        assert not self.thread.is_alive(), "Uvicorn owner failed to join"
        assert not self.errors, self.errors


@contextmanager
def loopback_server(root, *, actor=ALICE, **options):
    owner = LoopbackServer(root, actor=actor, **options)
    try:
        yield owner.start()
    finally:
        owner.close()


class SSEStream:
    """Read actual SSE wire frames, retaining comments for keepalive assertions."""

    def __init__(self, owner, cursor=None, *, timeout=3, headers=None):
        self.owner = owner
        self.headers = dict(headers or {})
        if cursor is not None:
            self.headers["Last-Event-ID"] = cursor
        self.timeout = timeout

    def __enter__(self):
        self.context = self.owner.client.stream(
            "GET", "/api/events", headers=self.headers, timeout=self.timeout
        )
        self.response = self.context.__enter__()
        try:
            assert self.response.status_code == 200
            assert self.response.headers["content-type"].startswith("text/event-stream")
            assert self.response.headers["cache-control"] == "no-store"
        except BaseException:
            self.context.__exit__(None, None, None)
            raise
        self.lines = self.response.iter_lines()
        return self

    def __exit__(self, *exc):
        return self.context.__exit__(*exc)

    def frame(self):
        fields, comments = {}, []
        for line in self.lines:
            if not line:
                if comments:
                    return {"comment": "\n".join(comments)}
                if not fields:
                    continue
                data = json.loads("\n".join(fields.pop("data")))
                frame = {**fields, "data": data}
                assert re.fullmatch(r"v1:[0-9a-f]{32}:[1-9][0-9]*", frame["id"])
                assert frame["id"] == data["cursor"]
                assert frame["id"] == f"v1:{data['generation']}:{data['seq']}"
                assert frame["event"] == data["kind"]
                assert {"at", "commit", "generation", "seq"} <= data.keys()
                return frame
            if line.startswith(":"):
                comments.append(line[1:].lstrip())
                continue
            name, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if name == "data":
                fields.setdefault(name, []).append(value)
            else:
                fields[name] = value
        raise AssertionError("SSE stream ended before its next frame")

    def event(self):
        while True:
            frame = self.frame()
            if "data" in frame:
                return frame["data"]
