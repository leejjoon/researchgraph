"""Optional loopback HTTP/SSE transport, with no graph validation or SQL paths."""

import ipaddress
import json
import os
import secrets
import socket
import stat
import webbrowser
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import anyio
import uvicorn
from starlette.applications import Starlette
from starlette.datastructures import Headers, MutableHeaders, QueryParams
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from researchgraph.events import ChangeSource
from researchgraph.interfaces import _dispatch, _prepare
from researchgraph.model import RuleViolation
from researchgraph.project import Project
from researchgraph.registry import OPERATIONS

_OPERATIONS = {operation["name"]: operation for operation in OPERATIONS}
_AUTHORITY = {
    "actor",
    "read_only",
    "project",
    "as_agent",
    "as_human",
    "preset",
    "json_output",
}
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _now():
    return datetime.now(UTC).replace(microsecond=0)


def _origin(value):
    parsed = urlsplit(value)
    host = parsed.hostname
    if (
        parsed.scheme != "http"
        or not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise RuleViolation("schema", "An HTTP loopback origin is required")
    try:
        if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
            raise ValueError
        port = parsed.port or 80
    except ValueError as error:
        raise RuleViolation("schema", "An HTTP loopback origin is required") from error
    return f"http://{parsed.netloc.lower()}", parsed.netloc.lower(), port


def _forbidden():
    return JSONResponse(
        {"rule": "authority", "message": "Forbidden", "ids": []}, status_code=403
    )


class _Unavailable(Exception):
    def __init__(self, error):
        self.error = error


class _State:
    def __init__(self, root, actor, role, read_only, origin):
        self.root = Path(root).expanduser().resolve()
        self.actor, self.role, self.read_only = actor, role, read_only
        self.origin, self.host, port = _origin(origin)
        self.cookie_name = f"researchgraph_session_{port}"
        self.launch_token, self.cookie = (
            secrets.token_urlsafe(32),
            secrets.token_urlsafe(32),
        )
        self.exchanged = False
        self.condition = anyio.Condition()
        self.project = self.source = self.error = None
        self.revision = 0
        self.cursor = None
        self.stopping = False

    def _open(self):
        if self.project is None:
            try:
                self.project = Project.open(self.root, read_only=self.read_only)
            except RuleViolation as error:
                raise _Unavailable(error) from error
            self.source = ChangeSource(self.project)
        return self.project

    def _close(self):
        if self.source is not None:
            self.source.close()
            self.source = None
        if self.project is not None:
            self.project.close()
            self.project = None

    def _notify(self, cursor):
        if cursor != self.cursor:
            self.cursor = cursor
            self.revision += 1
            self.condition.notify_all()

    def _observe(self):
        try:
            self._open()
            result = self.source.changes(actor=self.actor, now=_now(), role=self.role)
            self.error = None
            self._notify(result["cursor"])
        except (_Unavailable, RuleViolation) as error:
            self.error = error.error if isinstance(error, _Unavailable) else error
            self._close()
            self.revision += 1
            self.condition.notify_all()

    async def _poll(self):
        while True:
            await anyio.sleep(1)
            async with self.condition:
                if self.stopping:
                    return
                self._observe()


class _Boundary:
    def __init__(self, app, *, state):
        self.app, self.state = app, state

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        hosts, origins = headers.getlist("host"), headers.getlist("origin")
        valid_hosts = {self.state.host}
        if self.state.origin.endswith(":80"):
            valid_hosts.add(self.state.host.removesuffix(":80"))
        valid = len(hosts) == 1 and hosts[0].lower() in valid_hosts
        valid = (
            valid
            and len(origins) <= 1
            and (not origins or origins[0] == self.state.origin)
        )
        if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            valid = valid and origins == [self.state.origin]
        if {"token", "access_token", "session_token"} & QueryParams(
            scope.get("query_string", b"")
        ).keys():
            valid = False
        protected = scope["path"].startswith(("/api", "/artifacts"))
        exchange = scope["path"] == "/api/session" and scope["method"] == "POST"
        if protected and not exchange:
            cookie = Request(scope).cookies.get(self.state.cookie_name, "")
            valid = (
                valid
                and self.state.exchanged
                and secrets.compare_digest(cookie.encode(), self.state.cookie.encode())
            )

        async def guarded_send(message):
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers.update(_SECURITY_HEADERS)
            await send(message)

        if not valid:
            await _forbidden()(scope, receive, guarded_send)
        else:
            await self.app(scope, receive, guarded_send)


def _read_arguments(request, operation):
    schemas = {arg["name"]: arg["schema"] for arg in operation["arguments"]}
    schemas.update(now={"type": ["string", "null"]}, role={"type": ["string", "null"]})
    values = {}
    for name, raw in request.query_params.multi_items():
        if name in values:
            raise RuleViolation(
                "schema", "Repeated query keys require one JSON array value"
            )
        schema = schemas.get(name, {})
        types = schema.get("type", [])
        types = [types] if isinstance(types, str) else types
        structured = any(
            kind in types
            for kind in ("array", "object", "integer", "number", "boolean")
        )
        try:
            values[name] = (
                json.loads(raw)
                if structured or raw.startswith('"') or raw == "null"
                else raw
            )
        except (TypeError, ValueError) as error:
            raise RuleViolation("schema", "Invalid JSON query value") from error
    return values


def _open_artifact(project, node, relative):
    project._safe_path(relative)
    record = project.get_node(node).to_dict()
    if not any(artifact["path"] == relative for artifact in record["artifacts"]):
        raise FileNotFoundError
    parts = PurePosixPath(relative).parts
    if len(parts) < 3 or parts[:2] != ("artifacts", node):
        raise FileNotFoundError
    # Descriptor-relative, no-follow traversal holds containment even when a
    # validated path is replaced before open. Nonblocking avoids FIFO swaps.
    descriptor = os.open(project.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parts[:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        result = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
        )
        try:
            information = os.fstat(result)
            if not stat.S_ISREG(information.st_mode):
                raise FileNotFoundError
        except BaseException:
            os.close(result)
            raise
        return result, information.st_size
    finally:
        os.close(descriptor)


def _download_path(path):
    candidate = path
    while True:
        parts = PurePosixPath(candidate)
        if (
            not candidate
            or parts.is_absolute()
            or str(parts) != candidate
            or any(part in (".", "..") for part in parts.parts)
            or "\\" in candidate
            or "\x00" in candidate
        ):
            return False
        decoded = unquote(candidate)
        if decoded == candidate:
            return True
        candidate = decoded


class _Download(StreamingResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.file.close()


class _Server(uvicorn.Server):
    async def shutdown(self, sockets=None):
        """Finish SSE waits before Uvicorn waits for open HTTP connections."""
        await self.config.app.state.stop_streams()
        await super().shutdown(sockets=sockets)


def create_app(
    root, *, actor, role=None, read_only=False, origin="http://127.0.0.1:8765"
):
    """Build a fixed-authority loopback app; lifespan owns its serialized Project.

    Only the one-use fragment exchange grants the HttpOnly session cookie.
    Instantiate inside any trusted host; its actual bound origin must match.
    """
    Project._actor(actor)
    state = _State(root, actor, role, read_only, origin)

    async def _stop_streams():
        async with state.condition:
            state.stopping = True
            state.revision += 1
            state.condition.notify_all()

    @asynccontextmanager
    async def lifespan(app):
        try:
            async with anyio.create_task_group() as group:
                async with state.condition:
                    state._observe()
                group.start_soon(state._poll)
                try:
                    yield
                finally:
                    group.cancel_scope.cancel()
        finally:
            state._close()

    async def _root(request):
        return HTMLResponse(
            files("researchgraph")
            .joinpath("viewer/static/index.html")
            .read_text(encoding="utf-8"),
            headers={
                "Content-Security-Policy": "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            },
        )

    async def _server_error(request, error):
        # Starlette logs/re-raises unexpected failures. Its outer error middleware
        # otherwise bypasses our response-header middleware when sending a 500.
        return Response(
            "Internal Server Error", status_code=500, headers=_SECURITY_HEADERS
        )

    async def _asset(request):
        name = request.path_params["name"]
        if name not in {
            "bootstrap.js",
            "style.css",
            "research-graph.js",
            "inspector.js",
            "api.js",
            "dom.js",
        }:
            return Response(status_code=404)
        media = "text/javascript" if name.endswith(".js") else "text/css"
        return Response(
            files("researchgraph").joinpath("viewer", "static", name).read_bytes(),
            media_type=media,
        )

    async def _session(request):
        try:
            body = await request.json()
            token = body.get("token") if isinstance(body, dict) else None
        except ValueError:
            return _forbidden()
        async with state.condition:
            if (
                state.exchanged
                or not isinstance(token, str)
                or not secrets.compare_digest(
                    token.encode(), state.launch_token.encode()
                )
            ):
                return _forbidden()
            state.exchanged = True
        response = JSONResponse({"ok": True})
        response.set_cookie(
            state.cookie_name, state.cookie, httponly=True, samesite="strict", path="/"
        )
        return response

    async def _operations(request):
        return JSONResponse(
            list(OPERATIONS),
            headers={"X-Researchgraph-Read-Only": str(bool(read_only)).lower()},
        )

    async def _api(request):
        name = request.path_params["operation"]
        operation = _OPERATIONS.get(name)
        if operation is None:
            return JSONResponse(
                {"rule": "schema", "message": "Unknown operation", "ids": []},
                status_code=422,
            )
        if request.method != ("POST" if operation["kind"] == "write" else "GET"):
            return Response(status_code=405)
        try:
            try:
                arguments = (
                    await request.json()
                    if request.method == "POST"
                    else _read_arguments(request, operation)
                )
            except ValueError as error:
                raise RuleViolation("schema", "Invalid JSON request") from error
            if isinstance(arguments, dict) and arguments.keys() & _AUTHORITY:
                return _forbidden()
            declared_role = (
                arguments.pop("role", role) if isinstance(arguments, dict) else role
            )
            prepared, values, context = _prepare(
                name, arguments, actor=actor, role=declared_role, read_only=read_only
            )
            if name in {"init", "import"} and values.get("path") is not None:
                if not isinstance(values["path"], str):
                    raise RuleViolation("schema", "Destination must be a string")
                if Path(values["path"]).expanduser().resolve() != state.root:
                    return _forbidden()
            async with state.condition:
                try:
                    project = state._open() if name not in {"init", "import"} else None
                    headers = {
                        "X-Researchgraph-Read-Only": str(bool(read_only)).lower()
                    }
                    if name == "graph" and values.get("as_of") is None:
                        snapshot = state.source.snapshot(**context)
                        value = snapshot["graph"]
                        headers["X-Researchgraph-Cursor"] = snapshot["cursor"]
                        state._notify(snapshot["cursor"])
                    else:
                        value = _dispatch(
                            state.root,
                            prepared,
                            values,
                            context,
                            read_only=read_only,
                            project=project,
                        )
                    return JSONResponse(value, headers=headers)
                finally:
                    state._observe()
        except _Unavailable as error:
            return JSONResponse(
                {
                    "rule": error.error.rule,
                    "message": error.error.message,
                    "ids": error.error.ids,
                },
                status_code=503,
            )
        except RuleViolation as error:
            return JSONResponse(
                {"rule": error.rule, "message": error.message, "ids": error.ids},
                status_code=422,
            )

    async def _events(request):
        async with state.condition:
            if state.error is not None:
                error = state.error
                return JSONResponse(
                    {"rule": error.rule, "message": error.message, "ids": error.ids},
                    status_code=503,
                )
        cursor = request.headers.get("last-event-id", "")

        async def _stream():
            nonlocal cursor
            while True:
                async with state.condition:
                    if state.stopping or state.error is not None:
                        return
                    result = state.source.changes(
                        actor=actor, now=_now(), role=role, since=cursor
                    )
                    state._notify(result["cursor"])
                    events = result["events"]
                    revision = state.revision
                for event in events:
                    if state.stopping:
                        return
                    cursor = event["cursor"]
                    yield f"id: {cursor}\nevent: {event['kind']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                async with state.condition:
                    keepalive = False
                    if revision == state.revision:
                        with anyio.move_on_after(15) as timeout:
                            await state.condition.wait_for(
                                lambda revision=revision: (
                                    revision != state.revision
                                    or state.stopping
                                    or state.error is not None
                                )
                            )
                        keepalive = timeout.cancel_called
                if keepalive:
                    yield ": keepalive\n\n"

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    async def _artifact(request):
        node, path = request.path_params["node"], request.path_params["path"]
        if not _download_path(path):
            return Response(status_code=404)
        relative = f"artifacts/{node}/{path}"
        disposition = (
            f"attachment; filename*=UTF-8''{quote(PurePosixPath(path).name, safe='')}"
        )
        try:
            async with state.condition:
                descriptor, size = _open_artifact(state._open(), node, relative)
        except (_Unavailable, RuleViolation, OSError, ValueError):
            return Response(status_code=404)
        stream = os.fdopen(descriptor, "rb")

        async def _bytes():
            while content := await anyio.to_thread.run_sync(stream.read, 65536):
                yield content

        try:
            response = _Download(
                _bytes(),
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": disposition,
                    "Content-Length": str(size),
                },
            )
            response.file = stream
            return response
        except BaseException:
            stream.close()
            raise

    app = Starlette(
        routes=[
            Route("/", _root),
            Route("/assets/{name}", _asset),
            Route("/api/session", _session, methods=["POST"]),
            Route("/api/operations", _operations),
            Route("/api/events", _events),
            Route("/api/{operation}", _api, methods=["GET", "POST"]),
            Route("/artifacts/{node}/{path:path}", _artifact),
        ],
        middleware=[Middleware(_Boundary, state=state)],
        lifespan=lifespan,
        exception_handlers={Exception: _server_error},
    )
    app.state.launch_token = state.launch_token
    app.state.stop_streams = _stop_streams
    return app


def serve(
    root,
    *,
    actor,
    role=None,
    read_only=False,
    host="127.0.0.1",
    port=0,
    open_browser=False,
):
    """Bind loopback, print one fragment URL and serve until a normal shutdown."""
    bound_host = "127.0.0.1" if host == "localhost" else host
    try:
        address = ipaddress.ip_address(bound_host)
        if not address.is_loopback:
            raise ValueError
    except ValueError as error:
        raise RuleViolation("schema", "serve requires a loopback host") from error
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((bound_host, port))
        listener.listen(128)
        selected_port = listener.getsockname()[1]
        display_host = f"[{host}]" if address.version == 6 else host
        origin = f"http://{display_host}:{selected_port}"
        app = create_app(
            root, actor=actor, role=role, read_only=read_only, origin=origin
        )
        url = f"{origin}/#token={app.state.launch_token}"
        print(url, flush=True)
        if open_browser:
            webbrowser.open(url)
        config = uvicorn.Config(
            app,
            access_log=False,
            log_level="warning",
            proxy_headers=False,
            server_header=False,
            timeout_graceful_shutdown=1,
        )
        _Server(config).run(sockets=[listener])
