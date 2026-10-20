#define PY_SSIZE_T_CLEAN
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <Python.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* Fixed source discovery only: no file reads, hashing or task execution.
   The caller owns hash-work cancellation and publication after a complete scan. */
typedef struct {
    PyObject *path_error;
} ScanState;

static PyObject *join_path(PyObject *parent, const char *name) {
    const char *base = PyBytes_AS_STRING(parent);
    return PyBytes_FromFormat("%s%s%s", base,
                              PyBytes_GET_SIZE(parent) == 1 && base[0] == '/' ? "" : "/",
                              name);
}

static int io_error(PyObject *path) {
    PyErr_SetFromErrnoWithFilename(PyExc_OSError, PyBytes_AS_STRING(path));
    return -1;
}

/* Translate only this owned strict decode, never errors from the sink. */
static PyObject *decode_relative(ScanState *state, PyObject *relative) {
    PyObject *name = PyUnicode_DecodeUTF8(PyBytes_AS_STRING(relative),
                                        PyBytes_GET_SIZE(relative), "strict");
    if (!name && PyErr_ExceptionMatches(PyExc_UnicodeDecodeError)) {
        PyErr_Clear();
        PyErr_SetString(state->path_error, "Graph source paths must be UTF-8");
    }
    return name;
}

static int refuse_link(ScanState *state, PyObject *relative) {
    PyObject *name = decode_relative(state, relative);
    if (!name) return -1;
    PyErr_Format(state->path_error, "Symlink graph/history path: %U", name);
    Py_DECREF(name);
    return -1;
}

static int append_file(ScanState *state, PyObject *result, PyObject *records,
                       PyObject *relative, PyObject *parent, const char *name,
                       PyObject *root_name) {
    PyObject *key = NULL, *record = NULL, *absolute = NULL, *value = NULL;
    int rc = -1;
    key = decode_relative(state, relative);
    if (!key) goto done;
    record = PyDict_GetItemWithError(records, key); /* Borrowed while GIL held. */
    if (!record && PyErr_Occurred()) goto done;
    if (record) {
        if (!PyTuple_CheckExact(record) || PyTuple_GET_SIZE(record) != 2 ||
            !PyUnicode_CheckExact(PyTuple_GET_ITEM(record, 0)) ||
            !PyUnicode_CheckExact(PyTuple_GET_ITEM(record, 1))) {
            PyErr_SetString(PyExc_TypeError, "Previous records must contain two-string tuples");
            goto done;
        }
        /* A record from another root or with mismatched framing is not reusable.
           Compare Unicode slices without allocating a fresh cached path string. */
        PyObject *old_name = PyTuple_GET_ITEM(record, 0);
        PyObject *old_path = PyTuple_GET_ITEM(record, 1);
        Py_ssize_t prefix = PyUnicode_GET_LENGTH(root_name);
        int slash_root = prefix == 1 && PyUnicode_ReadChar(root_name, 0) == '/';
        Py_ssize_t offset = prefix + !slash_root;
        int equal = PyObject_RichCompareBool(old_name, key, Py_EQ);
        if (equal < 0) goto done;
        if (equal && PyUnicode_GET_LENGTH(old_path) == offset + PyUnicode_GET_LENGTH(key)) {
            int starts = PyUnicode_Tailmatch(old_path, root_name, 0, prefix, -1);
            int ends = PyUnicode_Tailmatch(old_path, key, offset, PY_SSIZE_T_MAX, 1);
            if (starts < 0 || ends < 0) goto done;
            if (starts && ends && (slash_root || PyUnicode_ReadChar(old_path, prefix) == '/')) {
                rc = PyList_Append(result, record);
                goto done;
            }
        }
    }
    absolute = join_path(parent, name);
    if (!absolute) goto done;
    value = PyUnicode_DecodeFSDefaultAndSize(PyBytes_AS_STRING(absolute),
                                           PyBytes_GET_SIZE(absolute));
    if (!value) goto done;
    record = PyTuple_Pack(2, key, value);
    if (!record) goto done;
    rc = PyList_Append(result, record);
    Py_DECREF(record);
 done:
    Py_XDECREF(key);
    Py_XDECREF(absolute);
    Py_XDECREF(value);
    return rc;
}

static int push_directory(PyObject *stack, PyObject *absolute, PyObject *relative) {
    PyObject *pair = PyTuple_Pack(2, absolute, relative);
    if (!pair) return -1;
    int rc = PyList_Append(stack, pair);
    Py_DECREF(pair);
    return rc;
}

static int emit_batch(PyObject *result, PyObject *sink, Py_ssize_t *emitted, int final) {
    if (sink == Py_None) return 0;
    Py_ssize_t count = PyList_GET_SIZE(result) - *emitted;
    if (!count || (!final && count < 512)) return 0;
    PyObject *batch = PyTuple_New(count);
    if (!batch) return -1;
    for (Py_ssize_t i = 0; i < count; i++) {
        PyTuple_SET_ITEM(batch, i, Py_NewRef(PyList_GET_ITEM(result, *emitted + i)));
    }
    PyObject *returned = PyObject_CallOneArg(sink, batch);
    Py_DECREF(batch);
    if (!returned) return -1;
    Py_DECREF(returned);
    *emitted += count;
    return 0;
}

/* d_type is an optimization of fresh readdir classification, not a requirement.
   POSIX systems without it use fresh, no-follow fstatat for every entry. */
static int entry_type(DIR *directory, struct dirent *entry, struct stat *info) {
#if defined(_DIRENT_HAVE_D_TYPE) || defined(__APPLE__) || defined(__FreeBSD__) || defined(__OpenBSD__) || defined(__NetBSD__) || defined(__DragonFly__)
#if defined(DT_REG) && defined(DT_DIR) && defined(DT_LNK) && defined(DT_UNKNOWN)
    if (entry->d_type == DT_REG) return 1;
    if (entry->d_type == DT_DIR) return 2;
    if (entry->d_type == DT_LNK) return 3;
    if (entry->d_type != DT_UNKNOWN) return 0;
#endif
#endif
    if (fstatat(dirfd(directory), entry->d_name, info, AT_SYMLINK_NOFOLLOW) < 0) return -1;
    return S_ISREG(info->st_mode) ? 1 : S_ISDIR(info->st_mode) ? 2 :
           S_ISLNK(info->st_mode) ? 3 : 0;
}

static PyObject *discover(PyObject *self, PyObject *args) {
    ScanState *state = PyModule_GetState(self);
    PyObject *root_arg = NULL, *root_name = NULL, *root = NULL, *records = NULL;
    PyObject *stack = NULL, *result = NULL, *graph = NULL, *graph_relative = NULL;
    PyObject *metadata = NULL, *archive = NULL, *archive_relative = NULL, *pair = NULL;
    PyObject *path_module = NULL, *sink = Py_None;
    Py_ssize_t emitted = 0;
    DIR *directory = NULL;
    struct stat info;
    if (!PyArg_ParseTuple(args, "UO!|O:discover", &root_arg, &PyDict_Type, &records, &sink)) return NULL;
    if (sink != Py_None && !PyCallable_Check(sink)) {
        PyErr_SetString(PyExc_TypeError, "Batch sink must be callable or None");
        return NULL;
    }
    /* Normalize once, preserving filesystem surrogate escaping in the root. */
    path_module = PyImport_ImportModule("posixpath");
    if (!path_module) goto error;
    root_name = PyObject_CallMethod(path_module, "abspath", "O", root_arg);
    Py_CLEAR(path_module);
    if (!root_name || !PyUnicode_FSConverter(root_name, &root)) goto error;
    stack = PyList_New(0);
    result = PyList_New(0);
    graph = join_path(root, "graph");
    graph_relative = PyBytes_FromString("graph");
    if (!stack || !result || !graph || !graph_relative) goto error;
    if (lstat(PyBytes_AS_STRING(graph), &info) == 0) {
        if (S_ISLNK(info.st_mode)) { refuse_link(state, graph_relative); goto error; }
        if (push_directory(stack, graph, graph_relative) < 0) goto error;
    } else if (errno != ENOENT && errno != ENOTDIR) {
        io_error(graph); goto error;
    }
    while (PyList_GET_SIZE(stack)) {
        Py_ssize_t index = PyList_GET_SIZE(stack) - 1;
        pair = Py_NewRef(PyList_GET_ITEM(stack, index));
        if (PyList_SetSlice(stack, index, index + 1, NULL) < 0) goto error;
        PyObject *absolute = PyTuple_GET_ITEM(pair, 0);
        PyObject *relative = PyTuple_GET_ITEM(pair, 1);
        /* Only one DIR is live; queued paths do not accumulate descriptors.
           Refuse replacement symlinks when opening a queued directory too. */
        int fd = open(PyBytes_AS_STRING(absolute), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (fd < 0) {
            int saved = errno;
            if (lstat(PyBytes_AS_STRING(absolute), &info) == 0 && S_ISLNK(info.st_mode)) {
                refuse_link(state, relative);
            } else {
                errno = saved;
                io_error(absolute);
            }
            goto error;
        }
        directory = fdopendir(fd);
        if (!directory) {
            int saved = errno;
            close(fd);
            errno = saved;
            io_error(absolute); goto error;
        }
        for (;;) {
            errno = 0;
            struct dirent *entry = readdir(directory);
            if (!entry) {
                if (errno) { io_error(absolute); goto error; }
                break;
            }
            if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
            int type = entry_type(directory, entry, &info);
            if (type < 0) {
                int saved = errno;
                PyObject *failed = join_path(absolute, entry->d_name);
                if (failed) { errno = saved; io_error(failed); Py_DECREF(failed); }
                goto error;
            }
            if (!type) continue;
            PyObject *child_relative = join_path(relative, entry->d_name);
            if (!child_relative) goto error;
            int rc;
            if (type == 3) {
                rc = refuse_link(state, child_relative);
            } else if (type == 2) {
                PyObject *child = join_path(absolute, entry->d_name);
                rc = child ? push_directory(stack, child, child_relative) : -1;
                Py_XDECREF(child);
            } else {
                rc = append_file(state, result, records, child_relative, absolute, entry->d_name, root_name);
                if (rc == 0) rc = emit_batch(result, sink, &emitted, 0);
            }
            Py_DECREF(child_relative);
            if (rc < 0) goto error;
        }
        int close_result = closedir(directory);
        directory = NULL;
        if (close_result < 0) { io_error(absolute); goto error; }
        Py_CLEAR(pair);
    }
    metadata = join_path(root, ".researchgraph");
    archive_relative = PyBytes_FromString(".researchgraph/history.jsonl");
    if (!metadata || !archive_relative) goto error;
    if (lstat(PyBytes_AS_STRING(metadata), &info) == 0) {
        if (S_ISLNK(info.st_mode)) { refuse_link(state, archive_relative); goto error; }
    } else if (errno != ENOENT && errno != ENOTDIR) {
        io_error(metadata); goto error;
    }
    archive = join_path(metadata, "history.jsonl");
    if (!archive) goto error;
    if (lstat(PyBytes_AS_STRING(archive), &info) == 0) {
        if (S_ISLNK(info.st_mode)) { refuse_link(state, archive_relative); goto error; }
        if (S_ISREG(info.st_mode) &&
            append_file(state, result, records, archive_relative, metadata, "history.jsonl", root_name) < 0) goto error;
    } else if (errno != ENOENT && errno != ENOTDIR) {
        io_error(archive); goto error;
    }
    if (emit_batch(result, sink, &emitted, 1) < 0) goto error;
    goto done;
 error:
    if (directory) {
        int saved = errno;
        closedir(directory);
        errno = saved;
    }
    Py_CLEAR(result);
 done:
    Py_XDECREF(path_module);
    Py_XDECREF(pair);
    Py_XDECREF(stack);
    Py_XDECREF(root_name);
    Py_XDECREF(root);
    Py_XDECREF(graph);
    Py_XDECREF(graph_relative);
    Py_XDECREF(metadata);
    Py_XDECREF(archive);
    Py_XDECREF(archive_relative);
    return result;
}

#define FRAMES_CAPSULE "researchgraph.store._native_scan.frames"

/* Only immutable framing and exact str/int dictionary entries are owned here.
   Current source hashes and source owners never enter this acyclic graph. */
typedef struct {
    PyObject *template;
    PyObject *indices;
    Py_ssize_t *offsets;
    Py_ssize_t count;
    Py_ssize_t size;
} Frames;

static void frames_free(Frames *frames) {
    if (!frames) return;
    Py_XDECREF(frames->template);
    Py_XDECREF(frames->indices);
    PyMem_Free(frames->offsets);
    PyMem_Free(frames);
}

static void frames_destroy(PyObject *capsule) {
    if (PyCapsule_IsValid(capsule, FRAMES_CAPSULE)) {
        frames_free(PyCapsule_GetPointer(capsule, FRAMES_CAPSULE));
    }
}

static PyObject *compile_frames(PyObject *self, PyObject *args) {
    PyObject *input = NULL, *paths = NULL;
    Frames *frames = NULL;
    if (!PyArg_ParseTuple(args, "OO:compile_frames", &input, &paths)) return NULL;
    if (!PyTuple_CheckExact(input) || !PyTuple_CheckExact(paths)) {
        PyErr_SetString(PyExc_TypeError, "Frames and paths must be exact tuples");
        return NULL;
    }
    Py_ssize_t count = PyTuple_GET_SIZE(input), total = 0;
    if (count != PyTuple_GET_SIZE(paths)) {
        PyErr_SetString(PyExc_ValueError, "Frame and path counts must match");
        return NULL;
    }
    for (Py_ssize_t i = 0; i < count; i++) {
        PyObject *frame = PyTuple_GET_ITEM(input, i);
        if (!PyBytes_CheckExact(frame) ||
            !PyUnicode_CheckExact(PyTuple_GET_ITEM(paths, i))) {
            PyErr_SetString(PyExc_TypeError, "Frames and paths require exact bytes and str");
            return NULL;
        }
        Py_ssize_t length = PyBytes_GET_SIZE(frame);
        if (length < 8) {
            PyErr_SetString(PyExc_ValueError, "Frame lacks its eight-byte length");
            return NULL;
        }
        const unsigned char *data = (const unsigned char *)PyBytes_AS_STRING(frame);
        uint64_t encoded = 0;
        for (int j = 0; j < 8; j++) encoded = (encoded << 8) | data[j];
        if (encoded != (uint64_t)(length - 8)) {
            PyErr_SetString(PyExc_ValueError, "Frame length does not match its bytes");
            return NULL;
        }
        if (length > PY_SSIZE_T_MAX - 32 || total > PY_SSIZE_T_MAX - length - 32) {
            PyErr_SetString(PyExc_ValueError, "Compiled frames exceed the bytes size limit");
            return NULL;
        }
        total += length + 32;
    }
    if ((size_t)count > SIZE_MAX / sizeof(Py_ssize_t)) {
        PyErr_SetString(PyExc_ValueError, "Frame offsets exceed the allocation limit");
        return NULL;
    }
    frames = PyMem_Calloc(1, sizeof(Frames));
    if (!frames) return PyErr_NoMemory();
    frames->count = count;
    frames->size = total;
    frames->indices = PyDict_New();
    if (!frames->indices) goto error;
    if (count) {
        frames->offsets = PyMem_Malloc((size_t)count * sizeof(Py_ssize_t));
        if (!frames->offsets) { PyErr_NoMemory(); goto error; }
    }
    frames->template = PyBytes_FromStringAndSize(NULL, total);
    if (!frames->template) goto error;
    char *buffer = PyBytes_AS_STRING(frames->template);
    Py_ssize_t offset = 0;
    for (Py_ssize_t i = 0; i < count; i++) {
        PyObject *path = PyTuple_GET_ITEM(paths, i);
        int present = PyDict_Contains(frames->indices, path);
        if (present < 0) goto error;
        if (present) {
            PyErr_SetString(PyExc_ValueError, "Canonical paths must be unique");
            goto error;
        }
        PyObject *index = PyLong_FromSsize_t(i);
        if (!index) goto error;
        int rc = PyDict_SetItem(frames->indices, path, index);
        Py_DECREF(index);
        if (rc < 0) goto error;
        PyObject *frame = PyTuple_GET_ITEM(input, i);
        Py_ssize_t length = PyBytes_GET_SIZE(frame);
        memcpy(buffer + offset, PyBytes_AS_STRING(frame), (size_t)length);
        offset += length;
        frames->offsets[i] = offset;
        memset(buffer + offset, 0, 32);
        offset += 32;
    }
    PyObject *capsule = PyCapsule_New(frames, FRAMES_CAPSULE, frames_destroy);
    if (!capsule) goto error;
    return capsule;
 error:
    frames_free(frames);
    return NULL;
}

static PyObject *frame_hashes(PyObject *self, PyObject *args) {
    PyObject *capsule = NULL, *batches = NULL, *result = NULL;
    unsigned char *seen = NULL;
    if (!PyArg_ParseTuple(args, "OO:frame_hashes", &capsule, &batches)) return NULL;
    if (!PyCapsule_IsValid(capsule, FRAMES_CAPSULE)) {
        PyErr_SetString(PyExc_TypeError, "Expected a compiled frames capsule");
        return NULL;
    }
    if (!PyList_CheckExact(batches) && !PyTuple_CheckExact(batches)) {
        PyErr_SetString(PyExc_TypeError, "Hash batches must be an exact list or tuple");
        return NULL;
    }
    Frames *frames = PyCapsule_GetPointer(capsule, FRAMES_CAPSULE);
    if (!frames) return NULL;
    size_t seen_size = (size_t)(frames->count / 8) + (frames->count % 8 != 0);
    if (seen_size) {
        seen = PyMem_Calloc(seen_size, 1);
        if (!seen) return PyErr_NoMemory();
    }
    result = PyBytes_FromStringAndSize(NULL, frames->size);
    if (!result) goto error;
    char *buffer = PyBytes_AS_STRING(result);
    memcpy(buffer, PyBytes_AS_STRING(frames->template), (size_t)frames->size);
    int is_list = PyList_CheckExact(batches);
    Py_ssize_t batch_count = is_list ? PyList_GET_SIZE(batches) : PyTuple_GET_SIZE(batches);
    Py_ssize_t received = 0;
    for (Py_ssize_t i = 0; i < batch_count; i++) {
        PyObject *batch = is_list ? PyList_GET_ITEM(batches, i) : PyTuple_GET_ITEM(batches, i);
        if (!PyTuple_CheckExact(batch) || PyTuple_GET_SIZE(batch) != 2) {
            PyErr_SetString(PyExc_TypeError, "Each hash batch must be an exact two-tuple");
            goto error;
        }
        PyObject *paths = PyTuple_GET_ITEM(batch, 0), *payload = PyTuple_GET_ITEM(batch, 1);
        if (!PyTuple_CheckExact(paths) || !PyBytes_CheckExact(payload)) {
            PyErr_SetString(PyExc_TypeError, "Hash batches require exact tuple paths and bytes payloads");
            goto error;
        }
        Py_ssize_t count = PyTuple_GET_SIZE(paths);
        if (count > PY_SSIZE_T_MAX / 32 || PyBytes_GET_SIZE(payload) != count * 32) {
            PyErr_SetString(PyExc_ValueError, "Hash payload must contain exactly 32 bytes per path");
            goto error;
        }
        const char *data = PyBytes_AS_STRING(payload);
        for (Py_ssize_t j = 0; j < count; j++) {
            PyObject *path = PyTuple_GET_ITEM(paths, j);
            if (!PyUnicode_CheckExact(path)) {
                PyErr_SetString(PyExc_TypeError, "Hash paths must be exact str");
                goto error;
            }
            PyObject *value = PyDict_GetItemWithError(frames->indices, path);
            if (!value) {
                if (!PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, "Unknown hash path");
                goto error;
            }
            Py_ssize_t index = PyLong_AsSsize_t(value);
            if (index == -1 && PyErr_Occurred()) goto error;
            unsigned char mask = (unsigned char)(1U << (index % 8));
            if (seen[index / 8] & mask) {
                PyErr_SetString(PyExc_ValueError, "Duplicate hash path");
                goto error;
            }
            seen[index / 8] |= mask;
            received++;
            memcpy(buffer + frames->offsets[index], data + j * 32, 32);
        }
    }
    if (received != frames->count) {
        PyErr_SetString(PyExc_ValueError, "Missing current source hashes");
        goto error;
    }
    PyMem_Free(seen);
    return result;
 error:
    PyMem_Free(seen);
    Py_XDECREF(result);
    return NULL;
}

static int scan_traverse(PyObject *module, visitproc visit, void *arg) {
    ScanState *state = PyModule_GetState(module);
    Py_VISIT(state->path_error);
    return 0;
}

static int scan_clear(PyObject *module) {
    ScanState *state = PyModule_GetState(module);
    Py_CLEAR(state->path_error);
    return 0;
}

static void scan_free(void *module) {
    scan_clear((PyObject *)module);
}

static int scan_exec(PyObject *module) {
    ScanState *state = PyModule_GetState(module);
    state->path_error = PyErr_NewException("researchgraph.store._native_scan.PathError", PyExc_ValueError, NULL);
    if (!state->path_error) return -1;
    return PyModule_AddObjectRef(module, "PathError", state->path_error);
}

static PyMethodDef methods[] = {
    {"discover", discover, METH_VARARGS, "Fresh paths with an optional fixed 512-record pipeline sink."},
    {"compile_frames", compile_frames, METH_VARARGS, "Own immutable canonical framing with empty SHA slots."},
    {"frame_hashes", frame_hashes, METH_VARARGS, "Assemble every current raw hash into canonical framing."},
    {NULL, NULL, 0, NULL}
};
static PyModuleDef_Slot slots[] = {
    {Py_mod_exec, scan_exec},
    {0, NULL}
};
static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_native_scan", NULL, sizeof(ScanState), methods,
    slots, scan_traverse, scan_clear, scan_free
};
PyMODINIT_FUNC PyInit__native_scan(void) {
    return PyModuleDef_Init(&module);
}
