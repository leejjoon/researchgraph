"""Required native contracts; direct imports intentionally fail before integration."""

import ctypes
import math
import os
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from researchgraph import _native_clone as clone
from researchgraph.store import _native_scan as scan
from tests.helpers import node

COLUMNS = (
    "author",
    "fields",
    "approvals",
    "artifacts",
    "tags",
    "budget",
    "usage",
    "lease",
)
MUTABLE_COLUMNS = ("author", "fields", "approvals", "artifacts", "tags")


def template(fields=None, tags=()):
    # Derive the DTO and its insertion order from the real serializer, not a
    # second native-specific schema. Only flat, absent-column templates qualify.
    canonical = node().to_dict()
    author = dict(canonical["author"])
    author.update(email="alice@example.test", model="model-α", version="v1")
    skeleton = dict(canonical)
    for key in COLUMNS:
        skeleton[key] = None
    return skeleton, author, {} if fields is None else fields, tags


def expected_row(entry):
    skeleton, author, fields, tags = entry
    result = dict(skeleton)
    result.update(
        author=dict(author),
        fields=dict(fields),
        approvals=[],
        artifacts=[],
        tags=list(tags),
    )
    return result


def assert_scalar_equal(actual, expected):
    assert type(actual) is type(expected)
    if type(expected) is float:
        if math.isnan(expected):
            assert math.isnan(actual)
        else:
            assert actual == expected
            if expected == 0:
                assert math.copysign(1, actual) == math.copysign(1, expected)
    else:
        assert actual == expected


def assert_row_equal(actual, expected):
    assert type(actual) is dict
    assert list(actual) == list(expected)
    for key, value in expected.items():
        if type(value) is dict:
            assert type(actual[key]) is dict
            assert list(actual[key]) == list(value)
            for field, scalar in value.items():
                assert_scalar_equal(actual[key][field], scalar)
        elif type(value) is list:
            assert type(actual[key]) is list
            assert actual[key] == value
        else:
            assert_scalar_equal(actual[key], value)


def test_clone_empty_and_capsule_is_opaque():
    compiled = clone.compile_templates([])
    assert type(compiled).__name__ == "PyCapsule"
    first = clone.clone_rows(compiled)
    second = clone.clone_rows(compiled)
    assert type(first) is list
    assert first == second == []
    assert first is not second


def test_clone_canonical_order_metadata_and_independent_mutable_columns():
    entry = template({"count": 3, "enabled": True}, ("α", "", "line\nbreak"))
    expected = expected_row(entry)
    assert list(expected) == list(node().to_dict())
    compiled = clone.compile_templates([entry, entry])
    first = clone.clone_rows(compiled)
    second = clone.clone_rows(compiled)
    assert type(first) is type(second) is list
    assert len(first) == len(second) == 2
    rows = first + second
    assert len({id(row) for row in rows}) == 4
    for row in rows:
        assert_row_equal(row, expected)
    columns = [row[key] for row in rows for key in MUTABLE_COLUMNS]
    assert len({id(value) for value in columns}) == len(columns)
    first[0]["author"]["email"] = "changed"
    first[0]["fields"]["count"] = 900
    first[0]["approvals"].append({"actor": "changed"})
    first[0]["artifacts"].append("changed")
    first[0]["tags"].append("changed")
    first[0]["title"] = "changed"
    first.append({})
    for row in [first[1], *second, *clone.clone_rows(compiled)]:
        assert_row_equal(row, expected)


def test_clone_private_input_copies_and_survives_input_deletion():
    entry = template({"meaning": "original"}, ("original",))
    expected = expected_row(entry)
    inputs = [entry]
    compiled = clone.compile_templates(inputs)
    entry[0].clear()
    entry[1].clear()
    entry[2]["meaning"] = "mutated"
    inputs.clear()
    del inputs, entry
    assert_row_equal(clone.clone_rows(compiled)[0], expected)


def test_clone_empty_mutable_columns_and_absent_actor_metadata():
    entry = template()
    for key in ("email", "model", "version"):
        entry[1].pop(key, None)
    compiled = clone.compile_templates([entry, entry])
    rows = clone.clone_rows(compiled) + clone.clone_rows(compiled)
    expected = expected_row(entry)
    for row in rows:
        assert_row_equal(row, expected)
    assert len({id(row["tags"]) for row in rows}) == 4
    rows[0]["tags"].append("new")
    assert all(row["tags"] == [] for row in rows[1:])


def test_clone_releases_source_containers_and_owned_scalar_refs_without_gc():
    marker = "unique native scalar " + str(id(object()))
    entry = template({"marker": marker}, (marker,))
    expected = expected_row(entry)
    inputs = [entry]
    containers = (inputs, entry, entry[0], entry[1], entry[2])
    # Numeric ownership observations require the runtime's refcount API.
    getrefcount = getattr(sys, "getrefcount", None)
    if getrefcount is not None:
        counts = [getrefcount(value) for value in containers]
        scalar_refs = getrefcount(marker)
    compiled = clone.compile_templates(inputs)
    if getrefcount is not None:
        assert [getrefcount(value) for value in containers] == counts
        assert getrefcount(marker) > scalar_refs
    del compiled
    if getrefcount is not None:
        assert getrefcount(marker) == scalar_refs
        assert [getrefcount(value) for value in containers] == counts

    compiled = clone.compile_templates(inputs)
    for column in entry[:3]:
        column.clear()
    inputs.clear()
    del inputs, entry, containers, column
    first = clone.clone_rows(compiled)
    second = clone.clone_rows(compiled)
    assert len(first) == len(second) == 1
    assert_row_equal(first[0], expected)
    assert_row_equal(second[0], expected)
    assert first[0] is not second[0]
    for key in MUTABLE_COLUMNS:
        assert first[0][key] is not second[0][key]
    first[0]["fields"]["marker"] = "changed"
    first[0]["tags"].clear()
    del compiled
    assert_row_equal(second[0], expected)


def test_clone_preserves_non_json_float_edges_and_exact_numeric_types():
    values = {
        "nan": float("nan"),
        "positive_infinity": float("inf"),
        "negative_infinity": -float("inf"),
        "negative_zero": -0.0,
        "positive_zero": 0.0,
        "integer_zero": 0,
        "false": False,
        "integer_one": 1,
        "true": True,
        "large": 2**200,
        "none": None,
    }
    entry = template(values)
    assert_row_equal(
        clone.clone_rows(clone.compile_templates([entry]))[0], expected_row(entry)
    )


JSON_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(),
)


@given(
    fields=st.dictionaries(st.text(), JSON_SCALARS, max_size=20),
    tags=st.lists(st.text(), max_size=20),
)
def test_clone_flat_property(fields, tags):
    entry = template(fields, tuple(tags))
    expected = expected_row(entry)
    compiled = clone.compile_templates([entry, entry])
    for row in clone.clone_rows(compiled):
        assert_row_equal(row, expected)
    fields.clear()
    for row in clone.clone_rows(compiled):
        assert_row_equal(row, expected)


class DictSubclass(dict):
    pass


class ListSubclass(list):
    pass


class TupleSubclass(tuple):
    pass


class StringSubclass(str):
    pass


class IntSubclass(int):
    pass


class FloatSubclass(float):
    pass


@pytest.mark.parametrize("outer", [None, (), {}, iter(()), ListSubclass()])
def test_clone_requires_exact_outer_list(outer):
    with pytest.raises(TypeError):
        clone.compile_templates(outer)


@pytest.mark.parametrize(
    "entry", [None, [], (), (1,), (1, 2, 3, 4, 5), TupleSubclass(({}, {}, {}, ()))]
)
def test_clone_requires_exact_four_tuple(entry):
    with pytest.raises(TypeError):
        clone.compile_templates([entry])


@pytest.mark.parametrize("column", range(4))
@pytest.mark.parametrize("bad", [None, [], DictSubclass(), TupleSubclass()])
def test_clone_rejects_invalid_column_containers(column, bad):
    entry = list(template())
    entry[column] = bad
    with pytest.raises(TypeError):
        clone.compile_templates([tuple(entry)])


@pytest.mark.parametrize("column", range(3))
@pytest.mark.parametrize(
    "bad",
    [
        [],
        {},
        (),
        set(),
        object(),
        StringSubclass("x"),
        IntSubclass(1),
        FloatSubclass(1.5),
    ],
)
def test_clone_rejects_nested_and_custom_scalars(column, bad):
    entry = template()
    entry[column]["invalid"] = bad
    with pytest.raises(TypeError):
        clone.compile_templates([entry])


@pytest.mark.parametrize("column", range(3))
@pytest.mark.parametrize("key", [1, None, StringSubclass("custom")])
def test_clone_requires_exact_string_dictionary_keys(column, key):
    entry = template()
    entry[column][key] = "value"
    with pytest.raises(TypeError):
        clone.compile_templates([entry])


@pytest.mark.parametrize("key", COLUMNS)
def test_clone_requires_all_none_placeholders(key):
    entry = template()
    del entry[0][key]
    with pytest.raises(ValueError):
        clone.compile_templates([entry])
    entry[0][key] = "not absent"
    with pytest.raises(ValueError):
        clone.compile_templates([entry])


@pytest.mark.parametrize(
    "tags", [["x"], TupleSubclass(("x",)), (1,), (None,), (StringSubclass("x"),)]
)
def test_clone_requires_exact_tuple_of_exact_string_tags(tags):
    with pytest.raises(TypeError):
        clone.compile_templates([template(tags=tags)])


@pytest.mark.parametrize("bad", [None, [], {}, (), 1, "capsule", object()])
def test_clone_rejects_non_capsule_handles(bad):
    with pytest.raises(TypeError):
        clone.clone_rows(bad)


def test_clone_rejects_wrong_name_capsule_without_dereferencing():
    with pytest.raises(TypeError):
        clone.clone_rows(object())
    # ctypes exposes the capsule-forging C API on CPython, not every runtime.
    pythonapi = getattr(ctypes, "pythonapi", None)
    if pythonapi is not None:
        capsule_new = pythonapi.PyCapsule_New
        capsule_new.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p)
        capsule_new.restype = ctypes.py_object
        wrong = capsule_new(1, b"researchgraph.test.wrong_capsule", None)
        with pytest.raises(ValueError):
            clone.clone_rows(wrong)
    compiled = clone.compile_templates([template()])
    assert_row_equal(clone.clone_rows(compiled)[0], expected_row(template()))


def test_clone_failed_compile_releases_partially_owned_rows():
    marker = "failed compile scalar " + str(id(object()))
    valid = template({"marker": marker})
    invalid = template()
    invalid[2]["invalid"] = []
    expected = expected_row(valid)
    # Immediate scalar release is observable only with a refcount API.
    getrefcount = getattr(sys, "getrefcount", None)
    if getrefcount is not None:
        baseline = getrefcount(marker)
    with pytest.raises(TypeError):
        clone.compile_templates([valid, invalid])
    if getrefcount is not None:
        assert getrefcount(marker) == baseline
    assert_row_equal(expected_row(valid), expected)
    compiled = clone.compile_templates([valid, valid])
    for column in valid[:3]:
        column.clear()
    del valid, invalid, column
    first = clone.clone_rows(compiled)
    second = clone.clone_rows(compiled)
    assert len(first) == len(second) == 2
    assert len({id(row) for row in first + second}) == 4
    for row in first + second:
        assert_row_equal(row, expected)
    for key in MUTABLE_COLUMNS:
        assert len({id(row[key]) for row in first + second}) == 4
    first[0]["fields"].clear()
    for row in [first[1], *second, *clone.clone_rows(compiled)]:
        assert_row_equal(row, expected)


def put(root, relative, content="content"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def assert_records(records, root, names):
    assert type(records) is list
    assert len(records) == len(names)
    assert {record[0] for record in records} == set(names)
    for record in records:
        assert type(record) is tuple
        assert len(record) == 2
        assert all(type(value) is str for value in record)
        relative, absolute = record
        relative.encode("utf-8", errors="strict")
        assert not relative.startswith("/")
        assert absolute == str(root / relative)
        assert os.path.isabs(absolute)


def fd_snapshot():
    # Only FD observation is conditional: discovery/error assertions always run.
    # /proc is Linux-specific; scandir's own descriptor closes before returning.
    if not os.path.isdir("/proc/self/fd"):
        return None
    with os.scandir("/proc/self/fd") as entries:
        return {entry.name for entry in entries}


def assert_fd_unchanged(before):
    if before is not None:
        assert fd_snapshot() == before


def test_scan_empty_graph_optional_history_and_no_callback(tmp_path):
    assert scan.discover(str(tmp_path), {}) == []
    (tmp_path / "graph").mkdir()
    assert scan.discover(str(tmp_path), {}, None) == []
    put(tmp_path, ".researchgraph/history.jsonl")
    put(tmp_path, ".researchgraph/unrelated")
    records = scan.discover(str(tmp_path), {})
    assert_records(records, tmp_path, [".researchgraph/history.jsonl"])
    batches = []
    assert scan.discover(str(tmp_path), {}, batches.append) == records
    assert batches == [tuple(records)]


def test_scan_unicode_newline_hidden_deep_and_long_paths(tmp_path):
    deep = "graph/" + "/".join(["nested"] * 40) + "/leaf"
    names = [
        "graph/α/한글.yml",
        "graph/line\nbreak",
        "graph/.hidden",
        deep,
        "graph/" + "x" * 240,
    ]
    for name in names:
        put(tmp_path, name)
    before = fd_snapshot()
    assert_records(scan.discover(str(tmp_path), {}), tmp_path, names)
    assert_fd_unchanged(before)


def test_scan_fresh_membership_types_and_exact_previous_tuple_reuse(tmp_path):
    for name in [
        "graph/keep",
        "graph/remove",
        "graph/rename",
        "graph/becomes-directory",
    ]:
        put(tmp_path, name)
    first = scan.discover(str(tmp_path), {})
    previous = {record[0]: record for record in first}
    (tmp_path / "graph/remove").unlink()
    (tmp_path / "graph/rename").rename(tmp_path / "graph/renamed")
    (tmp_path / "graph/becomes-directory").unlink()
    (tmp_path / "graph/becomes-directory").mkdir()
    put(tmp_path, "graph/becomes-directory/child")
    put(tmp_path, "graph/added")
    second = scan.discover(str(tmp_path), previous)
    assert_records(
        second,
        tmp_path,
        ["graph/keep", "graph/renamed", "graph/becomes-directory/child", "graph/added"],
    )
    assert (
        next(record for record in second if record[0] == "graph/keep")
        is previous["graph/keep"]
    )
    assert all(record is not previous["graph/rename"] for record in second)
    (tmp_path / "graph/becomes-directory/child").unlink()
    (tmp_path / "graph/becomes-directory").rmdir()
    put(tmp_path, "graph/becomes-directory")
    third = scan.discover(str(tmp_path), {record[0]: record for record in second})
    assert "graph/becomes-directory" in dict(third)
    assert "graph/becomes-directory/child" not in dict(third)


@pytest.mark.parametrize(
    "target",
    [
        "graph",
        "graph/link",
        "graph/directory",
        ".researchgraph",
        ".researchgraph/history.jsonl",
    ],
)
def test_scan_rejects_every_graph_and_history_symlink_even_when_cached(
    tmp_path, target
):
    outside = tmp_path / "outside"
    outside.mkdir()
    put(tmp_path, "outside/file")
    path = tmp_path / target
    path.parent.mkdir(parents=True, exist_ok=True)
    destination = outside / "file" if target.endswith(("link", "jsonl")) else outside
    path.symlink_to(destination, target_is_directory=destination.is_dir())
    name = (
        ".researchgraph/history.jsonl"
        if target.startswith(".researchgraph")
        else target
    )
    previous = {name: (name, str(path))}
    before = fd_snapshot()
    with pytest.raises(scan.PathError) as error:
        scan.discover(str(tmp_path), previous)
    assert isinstance(error.value, ValueError)
    assert str(error.value) == f"Symlink graph/history path: {name}"
    assert_fd_unchanged(before)


def test_scan_new_symlink_replaces_previously_regular_cached_file(tmp_path):
    path = put(tmp_path, "graph/file")
    previous = {record[0]: record for record in scan.discover(str(tmp_path), {})}
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(
        scan.PathError, match="^Symlink graph/history path: graph/file$"
    ):
        scan.discover(str(tmp_path), previous)


@pytest.mark.parametrize("cached", [False, True])
def test_scan_non_utf8_name_is_path_error_even_with_cache(tmp_path, cached):
    (tmp_path / "graph").mkdir()
    raw = os.fsencode(tmp_path / "graph") + b"/invalid-\xff"
    fd = os.open(raw, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(fd)
    relative = "graph/" + os.fsdecode(b"invalid-\xff")
    previous = {relative: (relative, os.fsdecode(raw))} if cached else {}
    before = fd_snapshot()
    with pytest.raises(scan.PathError) as error:
        scan.discover(str(tmp_path), previous)
    assert isinstance(error.value, ValueError)
    assert str(error.value) == "Graph source paths must be UTF-8"
    assert_fd_unchanged(before)


@pytest.mark.parametrize(
    "bad",
    [
        [],
        ("graph/file",),
        ("graph/file", 1),
        TupleSubclass(("graph/file", "path")),
        (StringSubclass("graph/file"), "path"),
    ],
)
def test_scan_rejects_nonexact_cached_records_for_present_files(tmp_path, bad):
    put(tmp_path, "graph/file")
    with pytest.raises(TypeError):
        scan.discover(str(tmp_path), {"graph/file": bad})


def test_scan_does_not_resurrect_absent_cache_entries(tmp_path):
    assert (
        scan.discover(
            str(tmp_path), {"graph/gone": ("graph/gone", str(tmp_path / "graph/gone"))}
        )
        == []
    )


def test_scan_cached_history_reuse_still_requires_fresh_membership(tmp_path):
    history = put(tmp_path, ".researchgraph/history.jsonl")
    first = scan.discover(str(tmp_path), {})
    previous = {record[0]: record for record in first}
    batches = []
    second = scan.discover(str(tmp_path), previous, batches.append)
    assert second[0] is first[0]
    assert batches[0][0] is first[0]
    history.unlink()
    assert scan.discover(str(tmp_path), previous) == []
    history.mkdir()
    assert scan.discover(str(tmp_path), previous) == []


def test_scan_non_directory_graph_preserves_oserror(tmp_path):
    put(tmp_path, "graph")
    before = fd_snapshot()
    with pytest.raises(OSError) as error:
        scan.discover(str(tmp_path), {})
    assert not isinstance(error.value, scan.PathError)
    assert error.value.errno is not None
    assert_fd_unchanged(before)


@pytest.mark.parametrize(
    "root,previous,sink",
    [(None, {}, None), (b"root", {}, None), ("root", [], None), ("root", {}, 42)],
)
def test_scan_rejects_invalid_arguments(root, previous, sink):
    with pytest.raises(TypeError):
        scan.discover(root, previous, sink)


def test_scan_more_than_eight_bounded_tuple_batches_and_final_history(tmp_path):
    count = 512 * 8 + 17
    names = [f"graph/file-{index}" for index in range(count)]
    for name in names:
        put(tmp_path, name)
    put(tmp_path, ".researchgraph/history.jsonl")
    names.append(".researchgraph/history.jsonl")
    batches = []
    before = fd_snapshot()
    records = scan.discover(str(tmp_path), {}, batches.append)
    assert_records(records, tmp_path, names)
    assert len(batches) == 9
    assert all(type(batch) is tuple and 0 < len(batch) <= 512 for batch in batches)
    assert all(len(batch) == 512 for batch in batches[:-1])
    assert batches[-1][-1][0] == ".researchgraph/history.jsonl"
    emitted = [record for batch in batches for record in batch]
    assert emitted == records
    assert all(left is right for left, right in zip(emitted, records))
    assert len({record[0] for record in emitted}) == len(names)
    assert set(scan.discover(str(tmp_path), {})) == set(records)
    assert_fd_unchanged(before)


@pytest.mark.parametrize(
    "exception",
    [RuntimeError("sink"), ValueError("sink"), UnicodeError("sink"), OSError("sink")],
)
def test_scan_late_callback_errors_preserve_identity_and_close_fds(tmp_path, exception):
    for index in range(1025):
        put(tmp_path, f"graph/{index}")
    batches = []

    def sink(batch):
        assert type(batch) is tuple
        batches.append(batch)
        if len(batches) == 2:
            raise exception

    before = fd_snapshot()
    with pytest.raises(type(exception)) as error:
        scan.discover(str(tmp_path), {}, sink)
    assert error.value is exception
    assert len(batches) == 2
    assert all(len(batch) == 512 for batch in batches)
    assert_fd_unchanged(before)
    assert len(scan.discover(str(tmp_path), {})) == 1025


def test_scan_late_source_oserror_is_not_translated_and_closes_fds(tmp_path):
    # Rename preserves entries in the already open directory. Whether its child
    # was queued before or after the callback, opening that child's old path fails.
    (tmp_path / "graph/queued-child").mkdir(parents=True)
    for index in range(513):
        put(tmp_path, f"graph/{index}")
    batches = []

    def sink(batch):
        batches.append(batch)
        if len(batches) == 1:
            (tmp_path / "graph").rename(tmp_path / "moved-graph")

    before = fd_snapshot()
    with pytest.raises(OSError) as error:
        scan.discover(str(tmp_path), {}, sink)
    assert not isinstance(error.value, scan.PathError)
    assert error.value.errno is not None
    assert batches and len(batches[0]) == 512
    assert_fd_unchanged(before)


def test_scan_late_history_path_error_after_graph_batches_closes_fds(tmp_path):
    for index in range(512):
        put(tmp_path, f"graph/{index}")
    batches = []

    def sink(batch):
        batches.append(batch)
        (tmp_path / ".researchgraph").symlink_to(tmp_path / "missing")

    before = fd_snapshot()
    with pytest.raises(scan.PathError) as error:
        scan.discover(str(tmp_path), {}, sink)
    assert (
        str(error.value) == "Symlink graph/history path: .researchgraph/history.jsonl"
    )
    assert len(batches) == 1
    assert_fd_unchanged(before)


class BytesSubclass(bytes):
    pass


def signature_frames(names):
    return tuple(
        len(encoded).to_bytes(8, "big") + encoded
        for encoded in (name.encode("utf-8") for name in names)
    )


def test_signature_frames_empty_and_opaque():
    compiled = scan.compile_frames((), ())
    assert type(compiled).__name__ == "PyCapsule"
    for batches in ([], ()):
        result = scan.frame_hashes(compiled, batches)
        assert type(result) is bytes and result == b""


@pytest.mark.parametrize("outer", [list, tuple])
def test_signature_frames_canonical_order_arbitrary_sha_and_independent_results(outer):
    names = (".researchgraph/history.jsonl", "graph/a", "graph/별\nline")
    frames = signature_frames(names)
    paths = tuple("/unused/" + name for name in names)
    compiled = scan.compile_frames(frames, paths)
    hashes = (bytes(range(32)), b"\x00" * 32, bytes(range(255, 223, -1)))
    batches = outer(
        [((paths[2], paths[0]), hashes[2] + hashes[0]), ((paths[1],), hashes[1])]
    )
    expected = b"".join(frame + value for frame, value in zip(frames, hashes))
    first = scan.frame_hashes(compiled, batches)
    second = scan.frame_hashes(compiled, batches)
    assert type(first) is type(second) is bytes
    assert first == second == expected
    assert first is not second
    changed = tuple(bytes(value ^ 255 for value in digest) for digest in hashes)
    fresh = scan.frame_hashes(compiled, [(paths, b"".join(changed))])
    assert fresh == b"".join(frame + value for frame, value in zip(frames, changed))
    assert fresh != first and first == second == expected
    assert scan.frame_hashes(compiled, [(paths, b"".join(hashes))]) == expected
    equal_paths = tuple(path.encode("utf-8").decode("utf-8") for path in paths)
    assert equal_paths == paths
    # PyPy may coalesce equal immutable strings; CPython exercises new keys.
    if sys.implementation.name == "cpython":
        assert all(left is not right for left, right in zip(paths, equal_paths))
    assert scan.frame_hashes(compiled, [(equal_paths, b"".join(hashes))]) == expected


def test_signature_frames_owned_metadata_survives_inputs_without_retaining_hashes():
    frames = signature_frames(("graph/unique-별\nname",))
    paths = ("/unused/graph/unique-별\nname",)
    expected_frame = frames[0]
    compiled = scan.compile_frames(frames, paths)
    del frames, paths
    paths = ("/unused/graph/unique-별\nname",)
    payload = bytes(range(32))
    batches = [(paths, payload)]
    getrefcount = getattr(sys, "getrefcount", None)
    if getrefcount is not None:
        before = [getrefcount(value) for value in (batches, batches[0], payload)]
    result = scan.frame_hashes(compiled, batches)
    if getrefcount is not None:
        assert [
            getrefcount(value) for value in (batches, batches[0], payload)
        ] == before
    batches.clear()
    del batches, payload
    assert result == expected_frame + bytes(range(32))
    assert (
        scan.frame_hashes(compiled, [(paths, b"z" * 32)]) == expected_frame + b"z" * 32
    )
    del compiled
    assert result == expected_frame + bytes(range(32))


@pytest.mark.parametrize(
    "frames,paths",
    [
        ([], ()),
        (TupleSubclass(), ()),
        ((), []),
        ((), TupleSubclass()),
        ((None,), ("/a",)),
        ((bytearray(b"frame"),), ("/a",)),
        ((BytesSubclass(b"frame"),), ("/a",)),
        (signature_frames(("a",)), (b"/a",)),
        (signature_frames(("a",)), (StringSubclass("/a"),)),
    ],
)
def test_signature_compile_requires_exact_metadata_types(frames, paths):
    with pytest.raises(TypeError):
        scan.compile_frames(frames, paths)


@pytest.mark.parametrize(
    "frames,paths",
    [
        (signature_frames(("a",)), ()),
        ((), ("/a",)),
        ((b"",), ("/a",)),
        ((b"\x00" * 7,), ("/a",)),
        ((b"\x00" * 8 + b"a",), ("/a",)),
        (((2).to_bytes(8, "big") + b"a",), ("/a",)),
        (((1).to_bytes(8, "big") + b"ab",), ("/a",)),
        ((b"\xff" * 8 + b"a",), ("/a",)),
        (signature_frames(("a", "b")), ("/same", "/same")),
    ],
)
def test_signature_compile_rejects_malformed_lengths_and_duplicate_paths(frames, paths):
    with pytest.raises(ValueError):
        scan.compile_frames(frames, paths)


@pytest.mark.parametrize("bad", [None, [], {}, (), 1, "capsule", object()])
def test_signature_assembly_rejects_invalid_token(bad):
    with pytest.raises((TypeError, ValueError)):
        scan.frame_hashes(bad, [])


def test_signature_assembly_rejects_foreign_capsule():
    with pytest.raises((TypeError, ValueError)):
        scan.frame_hashes(clone.compile_templates([]), [])


@pytest.mark.parametrize(
    "batches",
    [
        None,
        {},
        iter(()),
        ListSubclass(),
        TupleSubclass(),
        [None],
        [(("/a",),)],
        [(("/a",), b"x" * 32, None)],
        [[("/a",), b"x" * 32]],
        [TupleSubclass((("/a",), b"x" * 32))],
        [(["/a"], b"x" * 32)],
        [(TupleSubclass(("/a",)), b"x" * 32)],
        [((StringSubclass("/a"),), b"x" * 32)],
        [((b"/a",), b"x" * 32)],
        [(("/a",), bytearray(b"x" * 32))],
        [(("/a",), BytesSubclass(b"x" * 32))],
    ],
)
def test_signature_assembly_requires_exact_batch_types(batches):
    compiled = scan.compile_frames(signature_frames(("a",)), ("/a",))
    with pytest.raises((TypeError, ValueError)):
        scan.frame_hashes(compiled, batches)


@pytest.mark.parametrize(
    "batches",
    [
        [],
        [(("/a",), b"x" * 31)],
        [(("/a",), b"x" * 33)],
        [(("/a", "/b"), b"x" * 32)],
        [(("/a",), b"x" * 32)],
        [(("/unknown", "/b"), b"x" * 64)],
        [(("/a", "/a"), b"x" * 64)],
        [(("/a", "/b"), b"x" * 64), (("/a",), b"y" * 32)],
        [((), b"x"), (("/a", "/b"), b"x" * 64)],
    ],
)
def test_signature_assembly_rejects_incomplete_duplicate_unknown_or_wrong_sha(batches):
    frames = signature_frames(("a", "b"))
    paths = ("/a", "/b")
    compiled = scan.compile_frames(frames, paths)
    with pytest.raises((TypeError, ValueError)):
        scan.frame_hashes(compiled, batches)
    # A failed call must not leave seen-path flags or partial hashes in metadata.
    assert scan.frame_hashes(compiled, [(paths, b"z" * 64)]) == b"".join(
        frame + b"z" * 32 for frame in frames
    )


def test_signature_empty_metadata_rejects_unknown_hashes():
    compiled = scan.compile_frames((), ())
    with pytest.raises((TypeError, ValueError)):
        scan.frame_hashes(compiled, [(("/unknown",), b"x" * 32)])


@given(st.lists(st.binary(min_size=32, max_size=32), min_size=3, max_size=3))
def test_signature_assembly_preserves_every_arbitrary_sha_byte(hashes):
    frames = signature_frames(("graph/a", "graph/b", "graph/별\nline"))
    paths = ("/graph/a", "/graph/b", "/graph/별\nline")
    compiled = scan.compile_frames(frames, paths)
    batches = [((paths[index],), hashes[index]) for index in (2, 0, 1)]
    assert scan.frame_hashes(compiled, batches) == b"".join(
        frame + digest for frame, digest in zip(frames, hashes)
    )
