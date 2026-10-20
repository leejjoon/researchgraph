#define PY_SSIZE_T_CLEAN
#include <Python.h>

enum { AUTHOR, FIELDS, APPROVALS, ARTIFACTS, TAGS, BUDGET, USAGE, LEASE, KEY_COUNT };
typedef struct { PyObject *keys[KEY_COUNT]; } State;
typedef struct { PyObject *columns[4]; } Template;
typedef struct { Py_ssize_t count; Template *rows; } Compiled;
static const char capsule_name[] = "researchgraph._native_clone.compiled_templates.v1";

static void free_compiled(Compiled *compiled) {
    for (Py_ssize_t index = 0; index < compiled->count; index++)
        for (int column = 0; column < 4; column++)
            Py_XDECREF(compiled->rows[index].columns[column]);
    PyMem_Free(compiled->rows);
    PyMem_Free(compiled);
}

static void destroy_compiled(PyObject *capsule) {
    Compiled *compiled = PyCapsule_GetPointer(capsule, capsule_name);
    if (compiled == NULL) {
        PyErr_WriteUnraisable(capsule);
        return;
    }
    free_compiled(compiled);
}

static int scalar(PyObject *value) {
    return value == Py_None || PyUnicode_CheckExact(value)
        || PyLong_CheckExact(value) || PyFloat_CheckExact(value)
        || PyBool_Check(value);
}

static int flat(PyObject *mapping) {
    Py_ssize_t position = 0;
    PyObject *key, *value;
    while (PyDict_Next(mapping, &position, &key, &value)) {
        if (!PyUnicode_CheckExact(key) || !scalar(value)) return 0;
    }
    return 1;
}

static int validate(State *state, PyObject *entry) {
    if (!PyTuple_CheckExact(entry) || PyTuple_GET_SIZE(entry) != 4) {
        PyErr_SetString(PyExc_TypeError, "Each template must be an exact four-item tuple");
        return -1;
    }
    PyObject *skeleton = PyTuple_GET_ITEM(entry, 0);
    PyObject *author = PyTuple_GET_ITEM(entry, 1);
    PyObject *fields = PyTuple_GET_ITEM(entry, 2);
    PyObject *tags = PyTuple_GET_ITEM(entry, 3);
    if (!PyDict_CheckExact(skeleton) || !PyDict_CheckExact(author)
        || !PyDict_CheckExact(fields) || !PyTuple_CheckExact(tags)) {
        PyErr_SetString(PyExc_TypeError, "Expected three exact dictionaries and an exact tags tuple");
        return -1;
    }
    if (!flat(skeleton) || !flat(author) || !flat(fields)) {
        PyErr_SetString(PyExc_TypeError, "Template dictionaries must contain only string keys and immutable JSON scalars");
        return -1;
    }
    for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(tags); index++) {
        if (!PyUnicode_CheckExact(PyTuple_GET_ITEM(tags, index))) {
            PyErr_SetString(PyExc_TypeError, "Tags must contain exact strings");
            return -1;
        }
    }
    for (int field = 0; field < KEY_COUNT; field++) {
        PyObject *value = PyDict_GetItemWithError(skeleton, state->keys[field]);
        if (value == NULL) {
            if (!PyErr_Occurred())
                PyErr_Format(PyExc_ValueError, "Missing canonical skeleton key: %U", state->keys[field]);
            return -1;
        }
        if (value != Py_None) {
            PyErr_Format(PyExc_ValueError, "Skeleton column must be None: %U", state->keys[field]);
            return -1;
        }
    }
    return 0;
}

static PyObject *compile_templates(PyObject *module, PyObject *templates) {
    State *state = PyModule_GetState(module);
    if (state == NULL) return NULL;
    if (!PyList_CheckExact(templates)) {
        PyErr_SetString(PyExc_TypeError, "Expected an exact list of templates");
        return NULL;
    }
    Py_ssize_t count = PyList_GET_SIZE(templates);
    if ((size_t)count > (size_t)PY_SSIZE_T_MAX / sizeof(Template))
        return PyErr_NoMemory();
    Compiled *compiled = PyMem_Calloc(1, sizeof(Compiled));
    if (compiled == NULL) return PyErr_NoMemory();
    if (count != 0) {
        compiled->rows = PyMem_Calloc((size_t)count, sizeof(Template));
        if (compiled->rows == NULL) {
            PyMem_Free(compiled);
            return PyErr_NoMemory();
        }
    }
    compiled->count = count;
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *entry = PyList_GET_ITEM(templates, index);
        if (validate(state, entry) < 0) {
            free_compiled(compiled);
            return NULL;
        }
        for (int column = 0; column < 3; column++) {
            compiled->rows[index].columns[column] = PyDict_Copy(PyTuple_GET_ITEM(entry, column));
            if (compiled->rows[index].columns[column] == NULL) {
                free_compiled(compiled);
                return NULL;
            }
        }
        compiled->rows[index].columns[3] = Py_NewRef(PyTuple_GET_ITEM(entry, 3));
    }
    PyObject *capsule = PyCapsule_New(compiled, capsule_name, destroy_compiled);
    if (capsule == NULL) free_compiled(compiled);
    return capsule;
}

static PyObject *clone_rows(PyObject *module, PyObject *capsule) {
    State *state = PyModule_GetState(module);
    if (state == NULL) return NULL;
    if (!PyCapsule_CheckExact(capsule)) {
        PyErr_SetString(PyExc_TypeError, "Expected a compiled templates capsule");
        return NULL;
    }
    Compiled *compiled = PyCapsule_GetPointer(capsule, capsule_name);
    if (compiled == NULL) return NULL;
    PyObject *rows = PyList_New(compiled->count);
    if (rows == NULL) return NULL;
    for (Py_ssize_t index = 0; index < compiled->count; index++) {
        PyObject **columns = compiled->rows[index].columns;
        PyObject *row = PyDict_Copy(columns[0]);
        if (row == NULL) {
            Py_DECREF(rows);
            return NULL;
        }
        for (int field = AUTHOR; field <= TAGS; field++) {
            PyObject *copy;
            if (field == AUTHOR || field == FIELDS) {
                copy = PyDict_Copy(columns[field + 1]);
            } else if (field == TAGS && PyTuple_GET_SIZE(columns[3]) != 0) {
                copy = PySequence_List(columns[3]);
            } else {
                copy = PyList_New(0);
            }
            if (copy == NULL) {
                Py_DECREF(row);
                Py_DECREF(rows);
                return NULL;
            }
            int status = PyDict_SetItem(row, state->keys[field], copy);
            Py_DECREF(copy);
            if (status < 0) {
                Py_DECREF(row);
                Py_DECREF(rows);
                return NULL;
            }
        }
        PyList_SET_ITEM(rows, index, row);
    }
    return rows;
}

static int visit(PyObject *module, visitproc visit, void *arg) {
    State *state = PyModule_GetState(module);
    if (state != NULL)
        for (int field = 0; field < KEY_COUNT; field++) Py_VISIT(state->keys[field]);
    return 0;
}

static int clear(PyObject *module) {
    State *state = PyModule_GetState(module);
    if (state != NULL)
        for (int field = 0; field < KEY_COUNT; field++) Py_CLEAR(state->keys[field]);
    return 0;
}

static void release(void *module) { clear((PyObject *)module); }
static PyMethodDef methods[] = {
    {"compile_templates", compile_templates, METH_O, "Validate and privately own flat template columns for one epoch."},
    {"clone_rows", clone_rows, METH_O, "Eagerly clone private flat canonical row templates."},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT, "_native_clone", NULL, sizeof(State), methods,
    NULL, visit, clear, release
};

PyMODINIT_FUNC PyInit__native_clone(void) {
    PyObject *module = PyModule_Create(&definition);
    if (module == NULL) return NULL;
    State *state = PyModule_GetState(module);
    const char *names[KEY_COUNT] = {
        "author", "fields", "approvals", "artifacts", "tags", "budget", "usage", "lease"
    };
    for (int field = 0; field < KEY_COUNT; field++) {
        state->keys[field] = PyUnicode_InternFromString(names[field]);
        if (state->keys[field] == NULL) {
            Py_DECREF(module);
            return NULL;
        }
    }
    return module;
}
