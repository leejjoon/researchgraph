"""Registry metadata is exhaustively cross-checked against spec and Click."""

import json
from copy import deepcopy
from pathlib import Path

import click
import pytest
from jsonschema import validate

from researchgraph.cli import main
from researchgraph.registry import COMMON_OPTIONS, OPERATIONS
from tests.test_cli import run_console
from tests.test_docs import help_commands, help_options, spec_commands

ROOT = Path(__file__).resolve().parents[1]


def check_inventory(operations, spec):
    names = [op["name"] for op in operations]
    assert len(names) == len(set(names)), "duplicate registry operation"
    assert set(names) == spec["Writes"] | spec["Reads"], (
        "missing/extra registry operation"
    )
    for op in operations:
        assert op["kind"] in {"read", "write"}, "unknown registry kind"
        category = "Reads" if op["kind"] == "read" else "Writes"
        assert op["name"] in spec[category], "misclassified registry operation"
        assert op["result"].strip(), "missing result-shape note"
        args = [arg["name"] for arg in op["arguments"]]
        assert len(args) == len(set(args)), "duplicate registry argument"


def test_registry_matches_spec_and_actual_command_categories():
    spec = spec_commands((ROOT / "spec/operations.md").read_text())
    check_inventory(OPERATIONS, spec)
    assert len(OPERATIONS) == 35
    assert set(spec) == {"Writes", "Reads", "Streams", "Servers"}
    assert spec["Streams"] == spec["Servers"] == set()
    assert set(main.commands) == set().union(*spec.values())
    # Ensure the catalogue is data-only, not a source of execution callables.
    assert json.loads(json.dumps(OPERATIONS)) == list(OPERATIONS)


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda ops: ops[:-1], "missing/extra"),
        (lambda ops: [*ops, ops[0]], "duplicate registry operation"),
        (lambda ops: [dict(ops[0], kind="read"), *ops[1:]], "misclassified"),
        (lambda ops: [dict(ops[0], result=""), *ops[1:]], "result-shape"),
        (
            lambda ops: [
                dict(ops[0], arguments=[*ops[0]["arguments"], ops[0]["arguments"][0]]),
                *ops[1:],
            ],
            "duplicate registry argument",
        ),
    ],
)
def test_registry_refuses_missing_duplicate_and_misclassified_entries(change, message):
    spec = spec_commands((ROOT / "spec/operations.md").read_text())
    with pytest.raises(AssertionError, match=message):
        check_inventory(change(deepcopy(list(OPERATIONS))), spec)


def test_every_parameter_type_default_required_and_repeatable_matches_click():
    common = {arg["cli_name"]: arg for arg in COMMON_OPTIONS}
    for op in OPERATIONS:
        command = main.commands[op["name"]]
        catalogue = {arg["cli_name"]: arg for arg in op["arguments"]}
        assert set(catalogue).isdisjoint(common)
        catalogue.update(common)
        assert set(catalogue) == {p.name for p in command.params}
        required = [
            "placeholder"
            for p in command.params
            if isinstance(p, click.Argument) and p.required
        ]
        with command.make_context(op["name"], required) as context:
            for parameter in command.params:
                arg = catalogue[parameter.name]
                assert arg["required"] == parameter.required
                repeatable = parameter.multiple or parameter.nargs == -1
                assert arg["repeatable"] == repeatable
                actual = context.params[parameter.name]
                if not parameter.required:
                    expected = arg.get("default", None)
                    if isinstance(actual, tuple):
                        actual = list(actual)
                    assert actual == expected, (op["name"], parameter.name)
                schema = arg["schema"]
                if repeatable:
                    assert schema["type"] == "array"
                    schema = schema["items"]
                types = (
                    schema["type"]
                    if isinstance(schema["type"], list)
                    else [schema["type"]]
                )
                if arg["encoding"] == "json":
                    assert parameter.type.name == "text"
                    assert set(types) - {"null"} <= {"object", "integer"}
                else:
                    expected_type = {
                        "text": "string",
                        "path": "string",
                        "integer": "integer",
                        "boolean": "boolean",
                    }[parameter.type.name]
                    assert expected_type in types


@pytest.mark.cli
def test_installed_help_options_equal_registry_without_regrouping_commands():
    top = run_console(["--help"])
    assert top.exit_code == 0
    assert help_commands(top.stdout) == {op["name"] for op in OPERATIONS}
    assert "\nWrites:\n" not in top.stdout and "\nReads:\n" not in top.stdout
    for op in OPERATIONS:
        result = run_console([op["name"], "--help"])
        assert result.exit_code == 0
        expected = {"--help"}
        for arg in (*COMMON_OPTIONS, *op["arguments"]):
            expected.update(arg["options"])
        assert help_options(result.stdout) == expected


def test_structured_repeated_and_omitted_arguments_are_not_coerced_or_materialised():
    by_name = {op["name"]: op for op in OPERATIONS}
    args = {arg["name"]: arg for arg in by_name["propose"]["arguments"]}
    fields = {"nested": {"values": [1, False, None]}}
    validate(fields, args["fields"]["schema"])
    edges = [
        {"type": "blocks", "to": "ta-aaaaaaaa"},
        {"type": "cites", "to": "li-aaaaaaaa"},
    ]
    validate(edges, args["edges"]["schema"])
    priority = next(
        arg for arg in by_name["tag"]["arguments"] if arg["name"] == "priority"
    )
    assert "default" not in priority  # omission preserves, explicit null clears
    validate(None, priority["schema"])
    validate(0, priority["schema"])
