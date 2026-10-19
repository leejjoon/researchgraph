"""Mapping-only canonical YAML codec for graph node and edge files.

Ordering belongs here; profile validation and model construction do not. The
SafeDumper subclass is local so importing this module never changes PyYAML's
process-wide representers or resolver behavior.
"""

from collections.abc import Mapping
from os import PathLike
from pathlib import Path

import yaml

NODE_KEYS = (
    "id",
    "type",
    "title",
    "body",
    "author",
    "created",
    "fields",
    "status",
    "updated",
    "approvals",
    "artifacts",
    "budget",
    "usage",
    "tags",
    "priority",
    "lease",
)
EDGE_KEYS = (
    "id",
    "type",
    "from",
    "to",
    "author",
    "created",
    "fields",
    "retired",
    "retired_at",
)
ACTOR_KEYS = ("kind", "name", "email", "model", "version")
APPROVAL_KEYS = ("actor", "role", "at", "what", "note")
ARTIFACT_KEYS = ("path", "sha256", "bytes", "added_at", "added_by")
BUDGET_KEYS = ("tokens", "compute_seconds")
USAGE_KEYS = BUDGET_KEYS
LEASE_KEYS = ("actor", "acquired_at", "expires_at")


class _CanonicalDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

    def choose_scalar_style(self):
        style = super().choose_scalar_style()
        # PyYAML ordinarily prefers single quotes for ambiguous strings.
        return '"' if style == "'" else style

    def expect_document_end(self):
        # A keep-chomp literal marks PyYAML's stream open-ended. A graph file
        # has exactly one document, so no closing "..." marker is necessary.
        self.open_ended = False
        super().expect_document_end()


def _represent_string(dumper, value):
    style = None
    if "\n" in value:
        style = "|"
        if "\r" in value or any(line.endswith(" ") for line in value.split("\n")):
            style = '"'
    # YAML treats these Unicode separators as line breaks in plain/literal
    # scalars. Escaping them is necessary to preserve the original characters.
    if any(character in value for character in ("\x85", "\u2028", "\u2029")):
        style = '"'
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_CanonicalDumper.add_representer(str, _represent_string)


def _ordered(mapping, keys):
    """Order a declared subrecord, omitting absent/null subkeys."""
    if mapping is None:
        return None
    return {key: mapping[key] for key in keys if mapping.get(key) is not None}


def _actor(mapping):
    return _ordered(mapping, ACTOR_KEYS)


def _approval(mapping):
    result = _ordered(mapping, APPROVAL_KEYS)
    if result.get("actor") is not None:
        result["actor"] = _actor(result["actor"])
    return result


def _artifact(mapping):
    result = _ordered(mapping, ARTIFACT_KEYS)
    if result.get("added_by") is not None:
        result["added_by"] = _actor(result["added_by"])
    return result


def _lease(mapping):
    result = _ordered(mapping, LEASE_KEYS)
    if result is not None and result.get("actor") is not None:
        result["actor"] = _actor(result["actor"])
    return result


def _fields(value):
    if isinstance(value, Mapping):
        return {key: _fields(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_fields(item) for item in value]
    return value


def _canonical(mapping, keys):
    result = {key: mapping.get(key) for key in keys}
    result["author"] = _actor(result["author"])
    result["fields"] = _fields(result["fields"])
    if keys == NODE_KEYS:
        for key, convert in (("approvals", _approval), ("artifacts", _artifact)):
            if result[key] is not None:
                result[key] = [convert(record) for record in result[key]]
        for key in ("budget", "usage"):
            result[key] = _ordered(result[key], BUDGET_KEYS) or None
        result["lease"] = _lease(result["lease"])
    return result


def _dump(mapping, keys):
    return yaml.dump(
        _canonical(mapping, keys),
        Dumper=_CanonicalDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
        width=float("inf"),
        line_break="\n",
    )


# PyYAML exposes the same safe YAML 1.1 constructors in its optional native
# parser. Keep identical semantics when installed without the native extension.
_SAFE_LOADER = yaml.CSafeLoader if yaml.__with_libyaml__ else yaml.SafeLoader


def _load_mapping(source):
    result = yaml.load(source, Loader=_SAFE_LOADER)
    if not isinstance(result, dict):
        raise TypeError("A graph YAML document must be a mapping")
    return result


def _load(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        return _load_mapping(stream)


def load_node(path: str | PathLike[str]) -> dict:
    """Read a node mapping using safe YAML 1.1 scalar semantics."""
    return _load(path)


def load_edge(path: str | PathLike[str]) -> dict:
    """Read an edge mapping, including its profile-defined fields."""
    return _load(path)


def dump_node(mapping: Mapping) -> str:
    """Return a node mapping in canonical YAML form without mutating it."""
    return _dump(mapping, NODE_KEYS)


def dump_edge(mapping: Mapping) -> str:
    """Return an edge mapping in canonical YAML form without mutating it."""
    return _dump(mapping, EDGE_KEYS)
