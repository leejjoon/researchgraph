"""Canonical PAX/zstd archives and destination-independent archive validation.

No extraction API is used: every logical member is checked before the caller
creates a destination, and only the returned regular-file bytes are restored.
"""

import io
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

import yaml
import zstandard

from researchgraph.model import (
    Edge,
    Node,
    RuleViolation,
    Status,
    format_timestamp,
    parse_timestamp,
)
from researchgraph.profiles import Profile, version_tuple
from researchgraph.store.history import INTERVAL_KEYS, _merge_intervals
from researchgraph.store.io import EDGE_KEYS, NODE_KEYS, _load_mapping

_GRAPH_PATH = re.compile(
    r"graph/(nodes|edges)/([a-z]{2,4}-[a-z2-7]{8})\.yaml", re.ASCII
)


def _tar_info(name, size=0, *, directory=False):
    """Construct fixed metadata; tarfile alone supplies necessary local PAX."""
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    info.size = 0 if directory else size
    info.mode = 0o755 if directory else 0o644
    info.mtime = info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.pax_headers = {}
    return info


def encode(files, directories):
    """Encode sorted relative paths, including all directories, without stat data."""
    directories = set(directories)
    for name in files:
        directories.update(
            parent.as_posix() + "/"
            for parent in PurePosixPath(name).parents
            if parent != PurePosixPath(".")
        )
    output = io.BytesIO()
    with tarfile.open(
        fileobj=output,
        mode="w",
        format=tarfile.PAX_FORMAT,
        encoding="utf-8",
        errors="strict",
        pax_headers={},
    ) as archive:
        for name in sorted(set(files) | directories):
            directory = name.endswith("/")
            content = b"" if directory else files[name]
            archive.addfile(
                _tar_info(name, len(content), directory=directory),
                None if directory else io.BytesIO(content),
            )
    return zstandard.ZstdCompressor(level=3).compress(output.getvalue())


def encode_history(rows):
    """The history member is a projection, never a rewrite of source history."""
    return "".join(
        json.dumps({key: row[key] for key in INTERVAL_KEYS}, separators=(",", ":"))
        + "\n"
        for row in sorted(
            rows, key=lambda row: (row["node_id"], row["from_time"], row["status"])
        )
    ).encode("utf-8")


def _member_path(name, directory):
    logical = name.rstrip("/") if directory else name
    parts = logical.split("/")
    if not logical or any(part in ("", ".", "..") for part in parts) or "\0" in logical:
        raise ValueError("Unsafe archive member path")
    if directory:
        allowed = logical in {
            ".researchgraph",
            "graph",
            "graph/nodes",
            "graph/edges",
            "artifacts",
        }
        allowed = allowed or (parts[0] == "artifacts" and len(parts) >= 2)
    else:
        allowed = logical in {
            ".researchgraph/profile.yaml",
            ".researchgraph/history.jsonl",
        }
        allowed = allowed or _GRAPH_PATH.fullmatch(logical) is not None
        allowed = allowed or (parts[0] == "artifacts" and len(parts) >= 3)
    if not allowed:
        raise ValueError(f"Unexpected archive member: {name}")
    return logical


def _read_members(path):
    # Explicit EOF checking matters: a streaming decoder can otherwise silently
    # accept a truncated frame whose tar payload happened to end on a boundary.
    decoder = zstandard.ZstdDecompressor().decompressobj()
    output = io.BytesIO()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            output.write(decoder.decompress(chunk))
    if not decoder.eof:
        raise ValueError("Truncated zstd frame")
    if decoder.unused_data:
        raise ValueError("Trailing bytes after the zstd archive frame")
    output.seek(0)
    files, directories, seen = {}, set(), set()
    with tarfile.open(
        fileobj=output, mode="r:", encoding="utf-8", errors="strict"
    ) as archive:
        if archive.pax_headers:
            raise ValueError("Global PAX records are not export members")
        for info in archive:
            if info.type not in (tarfile.REGTYPE, tarfile.DIRTYPE):
                raise ValueError("Only regular files and directories may be restored")
            if set(info.pax_headers) - {"path", "size"} or archive.pax_headers:
                raise ValueError("Custom PAX records are not export members")
            name = _member_path(info.name, info.isdir())
            if name in seen:
                raise ValueError("Duplicate logical archive member")
            seen.add(name)
            if info.isdir():
                if info.size:
                    raise ValueError("Archive directory has a payload")
                directories.add(name)
            else:
                with archive.extractfile(info) as payload:
                    files[name] = payload.read()
        # A file must not also act as a parent directory, even when the archive
        # omitted explicit directory members.
        for name in seen:
            if any(
                parent.as_posix() in files for parent in PurePosixPath(name).parents
            ):
                raise ValueError("Archive file/directory path collision")
    return files, directories


def _timestamp(value, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise TypeError("Archive timestamps must be strings")
    parsed = parse_timestamp(value)
    if format_timestamp(parsed) != value:
        raise ValueError("Archive timestamps must use canonical UTC whole seconds")
    return parsed


def _record(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= set(value)
        or set(value) - set(required) - set(optional)
    ):
        raise ValueError("Invalid graph subrecord keys")


def _actor(value):
    _record(value, ("kind", "name"), ("email", "model", "version"))


def _graph_record(value, *, edge):
    keys = EDGE_KEYS if edge else NODE_KEYS
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("Graph records must contain the declared keys")
    _actor(value["author"])
    if not isinstance(value["fields"], dict):
        raise TypeError("Fields must be a mapping")
    times = [_timestamp(value["created"])]
    if edge:
        times.append(_timestamp(value["retired_at"], nullable=True))
    else:
        times.append(_timestamp(value["updated"]))
        if not isinstance(value["approvals"], list) or not isinstance(
            value["artifacts"], list
        ):
            raise ValueError("Approval and artifact logs must be lists")
        for approval in value["approvals"]:
            _record(approval, ("actor", "at", "what"), ("role", "note"))
            _actor(approval["actor"])
            times.append(_timestamp(approval["at"]))
        for artifact in value["artifacts"]:
            _record(artifact, ("path", "sha256", "bytes", "added_at", "added_by"))
            _actor(artifact["added_by"])
            times.append(_timestamp(artifact["added_at"]))
        if value["lease"] is not None:
            _record(value["lease"], ("actor", "acquired_at", "expires_at"))
            _actor(value["lease"]["actor"])
            times.extend(
                _timestamp(value["lease"][key]) for key in ("acquired_at", "expires_at")
            )
        for key in ("budget", "usage"):
            dimensions = value[key]
            if dimensions is not None and (
                not isinstance(dimensions, dict)
                or not dimensions
                or any(
                    k not in {"tokens", "compute_seconds"}
                    or type(v) is not int
                    or v < 0
                    for k, v in dimensions.items()
                )
            ):
                raise ValueError("Invalid budget/usage mapping")
        if not isinstance(value["tags"], list):
            raise ValueError("Tags must be a list")
    return (Edge.from_dict(value) if edge else Node.from_dict(value)), times


def _history(content, identifiers):
    rows, times = [], []
    for line in content.decode("utf-8").splitlines():
        row = json.loads(line)
        if not isinstance(row, dict) or set(row) != set(INTERVAL_KEYS):
            raise ValueError("Expected six history interval keys")
        if row["node_id"] not in identifiers or row["status"] not in {
            str(s) for s in Status
        }:
            raise ValueError("Invalid history node/status")
        for key in ("from_commit", "to_commit"):
            if row[key] is not None and not isinstance(row[key], str):
                raise ValueError("Invalid history commit")
        start = _timestamp(row["from_time"])
        end = _timestamp(row["to_time"], nullable=True)
        if end is not None and end < start:
            raise ValueError("Invalid history time range")
        if end is None and row["to_commit"] is not None:
            raise ValueError("Open history interval cannot have an end commit")
        rows.append(row)
        times.extend((start, end))
    return rows, times


def read(path):
    """Read and validate structure/profile/timestamps, with no destination effects."""
    try:
        files, directories = _read_members(path)
        pin = _load_mapping(files[".researchgraph/profile.yaml"].decode("utf-8"))
        if set(pin) != {"name", "version"} or not isinstance(pin["name"], str):
            raise ValueError("Invalid profile pin")
        version = version_tuple(pin["version"])
        profile = Profile.load(pin["name"])
        if version > version_tuple(profile.version):
            raise RuleViolation(
                "profile_too_new", "Archive profile is newer than installed"
            )
        nodes, edges, times = [], [], []
        for name, content in files.items():
            match = _GRAPH_PATH.fullmatch(name)
            if match is None:
                continue
            record, clocks = _graph_record(
                _load_mapping(content.decode("utf-8")), edge=match[1] == "edges"
            )
            if record.id != match[2]:
                raise ValueError("Graph identifier does not match member path")
            (edges if match[1] == "edges" else nodes).append(record)
            times.extend(clocks)
        rows, clocks = _history(
            files[".researchgraph/history.jsonl"], {node.id for node in nodes}
        )
        normalized_open = {
            row
            for row in _merge_intervals(
                [tuple(row[name] for name in INTERVAL_KEYS) for row in rows], ()
            )
            if row[5] is None
        }
        open_intervals = {}
        latest_closed = {}
        for row in rows:
            if row["to_time"] is None:
                key = tuple(row[name] for name in INTERVAL_KEYS)
                open_intervals.setdefault(row["node_id"], {})[key] = row
            else:
                latest_closed[row["node_id"]] = max(
                    latest_closed.get(row["node_id"], row["to_time"]), row["to_time"]
                )
        for node in nodes:
            candidates = open_intervals.get(node.id, {})
            if (
                len(candidates) != 1
                or next(iter(candidates.values()))["status"] != str(node.status)
                or next(iter(candidates)) not in normalized_open
                or latest_closed.get(node.id, "")
                > next(iter(candidates.values()))["from_time"]
            ):
                raise ValueError("Each node needs one matching open status interval")
        times.extend(clocks)
        return (
            files,
            directories,
            profile,
            pin["version"],
            nodes,
            edges,
            rows,
            max((t for t in times if t is not None), default=None),
        )
    except RuleViolation as exc:
        if exc.rule == "profile_too_new":
            raise
        raise RuleViolation("bad_archive", f"Invalid archive: {exc}") from exc
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        yaml.YAMLError,
        tarfile.TarError,
        zstandard.ZstdError,
    ) as exc:
        raise RuleViolation("bad_archive", f"Invalid archive: {exc}") from exc
