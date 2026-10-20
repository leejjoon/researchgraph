"""Live-byte hashing and bounded ownership of the helper's mapped resources."""

import hashlib
import io
import os
import resource
import struct
from types import SimpleNamespace

import pytest

from researchgraph.store import _native_scan, hash_worker


def sha(data):
    return hashlib.sha256(data).digest()


def assert_closed(entry):
    assert entry.mapping.closed


def fd_count():
    try:
        with os.scandir("/proc/self/fd") as descriptors:
            return sum(1 for _ in descriptors)
    except FileNotFoundError:
        return None


def test_live_full_bytes_and_same_metadata_edits(tmp_path):
    path = tmp_path / "source"
    content = b"a" * 20000
    path.write_bytes(content)
    with hash_worker.MappedFiles() as files:
        assert files.digest(path) == sha(content)
        entry = files._entries[path]
        original = path.stat()
        with path.open("r+b") as stream:
            stream.seek(len(content) - 1)
            stream.write(b"z")
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        assert files.digest(path) == sha(content[:-1] + b"z")
        assert files._entries[path] is entry
        with path.open("r+b") as stream:
            stream.write(b"b")
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        assert files.digest(path) == sha(b"b" + content[1:-1] + b"z")


def test_atomic_replacement_with_same_size_and_mtime(tmp_path):
    path = tmp_path / "source"
    path.write_bytes(b"before")
    with hash_worker.MappedFiles() as files:
        files.digest(path)
        old = files._entries[path]
        original = path.stat()
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"after!")
        os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
        replacement.replace(path)
        assert files.digest(path) == sha(b"after!")
        assert_closed(old)
        assert files._entries[path].identity[1] != original.st_ino


def test_grow_shrink_and_zero_release_old_resources(tmp_path):
    path = tmp_path / "source"
    path.write_bytes(b"start")
    with hash_worker.MappedFiles() as files:
        files.digest(path)
        for content in (b"larger contents", b"s", b"", b"again"):
            old = files._entries.get(path)
            path.write_bytes(content)
            assert files.digest(path) == sha(content)
            if old is not None:
                assert_closed(old)
            if not content:
                assert not files._entries
                assert files._mapped_bytes == 0


def test_unicode_and_newline_paths(tmp_path):
    path = tmp_path / "한글\nsource"
    path.write_bytes(bytes(range(256)))
    with hash_worker.MappedFiles() as files:
        assert files.digest(str(path)) == sha(bytes(range(256)))


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink"])
def test_invalid_path_releases_previously_cached_mapping(tmp_path, kind):
    path = tmp_path / "source"
    path.write_bytes(b"content")
    with hash_worker.MappedFiles() as files:
        files.digest(path)
        old = files._entries[path]
        path.unlink()
        if kind == "directory":
            path.mkdir()
        elif kind == "symlink":
            target = tmp_path / "target"
            target.write_bytes(b"content")
            path.symlink_to(target)
        with pytest.raises(OSError):
            files.digest(path)
        assert_closed(old)
        assert not files._entries


def test_lru_eviction_closes_descriptors_and_maps(tmp_path):
    paths = [tmp_path / str(i) for i in range(3)]
    for path in paths:
        path.write_bytes(b"data")
    baseline = fd_count()
    with hash_worker.MappedFiles(_capacity=2) as files:
        for path in paths[:2]:
            files.digest(path)
        first, second = (files._entries[path] for path in paths[:2])
        files.digest(paths[0])
        files.digest(paths[2])
        assert_closed(second)
        assert not first.mapping.closed
        if baseline is not None:
            assert fd_count() == baseline + 2
        retained = list(files._entries.values())
    for entry in retained:
        assert_closed(entry)
    if baseline is not None:
        assert fd_count() == baseline
    assert files._mapped_bytes == 0
    files.close()
    with pytest.raises(ValueError):
        files.digest(paths[0])


def test_page_rounded_byte_budget_evicts(tmp_path):
    page = hash_worker.mmap.PAGESIZE
    with hash_worker.MappedFiles(_byte_budget=page * 2) as files:
        retained = []
        for i in range(3):
            path = tmp_path / str(i)
            path.write_bytes(b"x")
            files.digest(path)
            retained.append(files._entries[path])
            assert files._mapped_bytes <= page * 2
        assert_closed(retained[0])
        assert len(files._entries) == 2
        assert files._mapped_bytes == page * 2


def test_zero_and_oversized_files_stream_all_bytes_without_caching(
    tmp_path, monkeypatch
):
    page = hash_worker.mmap.PAGESIZE
    path = tmp_path / "large"
    content = b"x" * (16 * 1024 * 1024 + 17) + b"late bytes"
    path.write_bytes(content)
    calls = []
    original = hash_worker.hash_file

    def stream(source):
        calls.append(source)
        return original(source)

    monkeypatch.setattr(hash_worker, "hash_file", stream)
    with hash_worker.MappedFiles(_byte_budget=page) as files:
        assert files.digest(path) == sha(content)
        path.write_bytes(b"")
        assert files.digest(path) == sha(b"")
        assert not files._entries
        assert files._mapped_bytes == 0
    assert calls == [path, path]


def test_fd_limit_reserves_headroom_for_mapping_duplicates(monkeypatch):
    monkeypatch.setattr(resource, "getrlimit", lambda limit: (40, 40))
    with hash_worker.MappedFiles(_capacity=1024) as files:
        assert files._capacity == 8


def test_stable_paths_stat_paths_and_hash_every_byte_each_time(tmp_path, monkeypatch):
    path = tmp_path / "source"
    content = b"all bytes" * 2048
    path.write_bytes(content)
    expected = sha(content)
    original_identity = _native_scan.fresh_file_identity
    original_fstat = os.fstat
    original_sha = hashlib.sha256
    stats, fstats, hashed = [], [], []

    def fresh_identity(source):
        stats.append(source)
        return original_identity(source)

    def fstat(descriptor):
        fstats.append(descriptor)
        return original_fstat(descriptor)

    def digest(data):
        hashed.append(len(data))
        return original_sha(data)

    monkeypatch.setattr(_native_scan, "fresh_file_identity", fresh_identity)
    monkeypatch.setattr(hash_worker.os, "fstat", fstat)
    monkeypatch.setattr(hash_worker.hashlib, "sha256", digest)
    with hash_worker.MappedFiles() as files:
        for _ in range(3):
            assert files.digest(path) == expected
    assert stats == [path] * 3
    assert len(fstats) == 2  # Both checks occur only when opening a new mapping.
    assert hashed == [len(content)] * 3


def test_original_descriptor_closed_and_reuse_cannot_affect_live_mapping(
    tmp_path, monkeypatch
):
    path = tmp_path / "source"
    path.write_bytes(b"content")
    opened = []
    original_open = os.open

    def open_file(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(hash_worker.os, "open", open_file)
    with hash_worker.MappedFiles() as files:
        files.digest(path)
        entry = files._entries[path]
        with pytest.raises(OSError):
            os.fstat(opened[0])
        other = tmp_path / "other"
        other.write_bytes(b"unrelated")
        reused = original_open(other, os.O_RDONLY)
        try:
            if reused != opened[0]:
                os.dup2(reused, opened[0])
            original = path.stat()
            with path.open("r+b") as stream:
                stream.write(b"changed")
            os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))

            def failed_fstat(descriptor):
                pytest.fail("Cached hits must not inspect the closed original FD")

            monkeypatch.setattr(hash_worker.os, "fstat", failed_fstat)
            assert files.digest(path) == sha(b"changed")
            assert files._entries[path] is entry
        finally:
            if reused != opened[0]:
                os.close(opened[0])
            os.close(reused)


def test_native_identity_called_for_mapping_hits_with_changed_live_bytes(
    tmp_path, monkeypatch
):
    path = tmp_path / "source"
    path.write_bytes(b"before")
    original_identity = _native_scan.fresh_file_identity
    calls = []

    def fresh_identity(source):
        calls.append(source)
        return original_identity(source)

    monkeypatch.setattr(_native_scan, "fresh_file_identity", fresh_identity)
    with hash_worker.MappedFiles() as files:
        assert files.digest(path) == sha(b"before")
        entry = files._entries[path]
        stamp = path.stat()
        path.write_bytes(b"after!")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert files.digest(path) == sha(b"after!")
        assert files._entries[path] is entry
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"atomic")
        replacement.replace(path)
        assert files.digest(path) == sha(b"atomic")
        assert_closed(entry)
    assert calls == [path] * 3


def test_native_identity_failure_releases_cached_mapping(tmp_path, monkeypatch):
    path = tmp_path / "source"
    path.write_bytes(b"content")
    failure = PermissionError("fresh metadata denied")

    def failed_identity(source):
        assert source == path
        raise failure

    with hash_worker.MappedFiles() as files:
        assert files.digest(path) == sha(b"content")
        entry = files._entries[path]
        monkeypatch.setattr(_native_scan, "fresh_file_identity", failed_identity)
        with pytest.raises(PermissionError) as raised:
            files.digest(path)
        assert raised.value is failure
        assert_closed(entry)
        assert not files._entries
        assert files._mapped_bytes == 0


def test_descriptor_validation_error_closes_original_without_fallback(
    tmp_path, monkeypatch
):
    path = tmp_path / "source"
    path.write_bytes(b"content")
    opened = []
    original_open = os.open

    def open_file(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def failed_fstat(descriptor):
        raise OSError("descriptor failed")

    monkeypatch.setattr(hash_worker.os, "open", open_file)
    with hash_worker.MappedFiles() as files:
        with monkeypatch.context() as patch:
            patch.setattr(hash_worker.os, "fstat", failed_fstat)
            with pytest.raises(OSError, match="descriptor failed"):
                files.digest(path)
        assert not files._entries
        with pytest.raises(OSError):
            os.fstat(opened[0])


def test_open_and_mapping_errors_are_not_stream_fallbacks(tmp_path, monkeypatch):
    path = tmp_path / "source"
    path.write_bytes(b"content")

    def forbidden_stream(source):
        pytest.fail("Resource errors must not silently fall back to streaming")

    monkeypatch.setattr(hash_worker, "hash_file", forbidden_stream)
    with hash_worker.MappedFiles() as files:
        original = hash_worker.os.open
        with monkeypatch.context() as patch:
            patch.setattr(
                hash_worker.os,
                "open",
                lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")),
            )
            with pytest.raises(PermissionError):
                files.digest(path)
        descriptors = []

        def opened(*args, **kwargs):
            descriptor = original(*args, **kwargs)
            descriptors.append(descriptor)
            return descriptor

        monkeypatch.setattr(hash_worker.os, "open", opened)
        monkeypatch.setattr(
            hash_worker.mmap,
            "mmap",
            lambda *a, **k: (_ for _ in ()).throw(OSError("mapping failed")),
        )
        with pytest.raises(OSError, match="mapping failed"):
            files.digest(path)
        assert not files._entries
        for descriptor in descriptors:
            with pytest.raises(OSError):
                os.fstat(descriptor)


def test_context_error_closes_all_resources(tmp_path):
    path = tmp_path / "source"
    path.write_bytes(b"content")
    with pytest.raises(RuntimeError), hash_worker.MappedFiles() as files:
        files.digest(path)
        entry = files._entries[path]
        raise RuntimeError("caller failed")
    assert_closed(entry)


@pytest.mark.parametrize("error", [False, True])
def test_worker_eof_or_error_closes_all_resources(tmp_path, monkeypatch, error):
    import json

    path = tmp_path / "한글\nsource"
    path.write_bytes(b"content")
    entries = []
    original = hash_worker.MappedFiles.digest

    def digest(self, source):
        result = original(self, source)
        entries.append(self._entries[source])
        return result

    monkeypatch.setattr(hash_worker.MappedFiles, "digest", digest)
    request = json.dumps([str(path)]).encode() + b"\n"
    if error:
        request += b"{}\n"
    output = io.BytesIO()
    monkeypatch.setattr(
        hash_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(request))
    )
    monkeypatch.setattr(hash_worker.sys, "stdout", SimpleNamespace(buffer=output))
    hash_worker.main()
    frame = output.getvalue()
    assert struct.unpack(">I", frame[:4])[0] == 33
    assert frame[4:37] == b"\x00" + sha(b"content")
    if error:
        assert frame[41:42] == b"\x01"
    for entry in entries:
        assert_closed(entry)


def test_repeated_wire_reuses_only_paths_and_hashes_changed_live_bytes(
    tmp_path, monkeypatch
):
    import json

    path = tmp_path / "한글\nsource"
    path.write_bytes(b"before")
    request = json.dumps([str(path)]).encode() + b"\n"
    original_stat = path.stat()

    def requests():
        yield request
        path.write_bytes(b"after!")
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        yield request

    decoded = []
    original_loads = json.loads

    def loads(line):
        decoded.append(line)
        return original_loads(line)

    monkeypatch.setattr(hash_worker.json, "loads", loads)
    output = io.BytesIO()
    monkeypatch.setattr(hash_worker.sys, "stdin", SimpleNamespace(buffer=requests()))
    monkeypatch.setattr(hash_worker.sys, "stdout", SimpleNamespace(buffer=output))
    hash_worker.main()
    assert decoded == [request]
    assert output.getvalue() == b"".join(
        struct.pack(">I", 33) + b"\x00" + sha(content)
        for content in (b"before", b"after!")
    )


@pytest.mark.parametrize("invalid", [b"{}\n", b"[1]\n", b"[null]\n", b"not json\n"])
def test_different_wire_decodes_and_validates_again(tmp_path, monkeypatch, invalid):
    import json

    paths = [tmp_path / "first", tmp_path / "second"]
    for i, path in enumerate(paths):
        path.write_bytes(bytes([i]))
    requests = [json.dumps([str(path)]).encode() + b"\n" for path in paths]
    decoded = []
    original_loads = json.loads

    def loads(line):
        decoded.append(line)
        return original_loads(line)

    monkeypatch.setattr(hash_worker.json, "loads", loads)
    output = io.BytesIO()
    monkeypatch.setattr(
        hash_worker.sys, "stdin", SimpleNamespace(buffer=iter([*requests, invalid]))
    )
    monkeypatch.setattr(hash_worker.sys, "stdout", SimpleNamespace(buffer=output))
    hash_worker.main()
    assert decoded == [*requests, invalid]
    result = output.getvalue()
    assert result[:74] == b"".join(
        struct.pack(">I", 33) + b"\x00" + sha(bytes([i])) for i in range(2)
    )
    assert result[78:79] == b"\x01"


def test_empty_requests_retain_no_resources(monkeypatch):
    instances = []
    original_init = hash_worker.MappedFiles.__init__

    def init(self):
        original_init(self)
        instances.append(self)

    monkeypatch.setattr(hash_worker.MappedFiles, "__init__", init)
    output = io.BytesIO()
    monkeypatch.setattr(
        hash_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"[]\n[]\n"))
    )
    monkeypatch.setattr(hash_worker.sys, "stdout", SimpleNamespace(buffer=output))
    hash_worker.main()
    assert output.getvalue() == (struct.pack(">I", 1) + b"\x00") * 2
    assert not instances[0]._entries
    assert instances[0]._mapped_bytes == 0
