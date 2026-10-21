"""Bounded persisted-state polling, independent of write hooks and cache epochs."""

import re
import secrets
from collections import deque
from copy import deepcopy

from researchgraph.model import format_timestamp
from researchgraph.store.io import _canonical_equal

_CURSOR = re.compile(r"v1:([0-9a-f]{32}):([1-9][0-9]*)", re.ASCII)


class ChangeSource:
    """Observe one borrowed Project with a bounded in-memory cursor window.

    Each instance has an independent random generation; it never persists an
    event log. Calls are synchronous and must be serialized by their caller.
    close releases retained snapshots/events, not the borrowed Project. The
    owner must close that Project when a stream/server disconnects or stops.
    """

    def __init__(self, project, *, retention=1024):
        """Borrow project and retain at most retention positive-count envelopes."""
        if type(retention) is not int or retention < 1:
            raise ValueError("retention must be a positive integer")
        self._project = project
        self._events = deque(maxlen=retention)
        self._generation = secrets.token_hex(16)
        self._seq = 0
        self._graph = None
        self._fingerprint = None
        self._clock = None
        self._commit = None

    def close(self):
        """Idempotently discard retained state without closing the borrowed owner."""
        self._events.clear()
        self._graph = None
        self._fingerprint = None
        self._clock = None
        self._commit = None
        self._project = None

    def _emit(self, now, kind, **values):
        self._seq += 1
        event = {
            "cursor": f"v1:{self._generation}:{self._seq}",
            "seq": self._seq,
            "generation": self._generation,
            "at": format_timestamp(now),
            "commit": self._commit,
            "kind": kind,
            **values,
        }
        self._events.append(event)
        return event

    def _index(self, now, *, reset=False):
        return self._emit(
            now,
            "index",
            counts={kind: len(records) for kind, records in self._graph.items()},
            **({"reset": True} if reset else {}),
        )

    def _observe(self, *, actor, now, role):
        if self._project is None:
            raise ValueError("ChangeSource is closed")
        graph, fingerprint, commit, clock = self._project._observed_state(
            actor=actor, now=now, role=role
        )
        previous = self._graph
        bytes_changed = self._fingerprint != fingerprint
        clock_changed = self._clock != clock
        self._graph, self._fingerprint = graph, fingerprint
        self._commit, self._clock = commit, clock
        if previous is None:
            return [self._index(now)]
        if not bytes_changed:
            return [self._index(now)] if clock_changed else []
        old = {
            kind: {record["id"]: record for record in rows}
            for kind, rows in previous.items()
        }
        current = {
            kind: {record["id"]: record for record in rows}
            for kind, rows in graph.items()
        }
        if any(old[kind].keys() - current[kind].keys() for kind in old):
            return [self._index(now, reset=True)]
        events = []
        for node in graph["nodes"]:
            before = old["nodes"].get(node["id"])
            if before is None or not _canonical_equal(before, node):
                events.append(
                    self._emit(
                        now,
                        "node",
                        change="created" if before is None else "updated",
                        id=node["id"],
                        previous_status=None if before is None else before["status"],
                        node=node,
                    )
                )
        for edge in graph["edges"]:
            before = old["edges"].get(edge["id"])
            if before is None or not _canonical_equal(before, edge):
                events.append(
                    self._emit(
                        now,
                        "edge",
                        change="retired" if edge["retired"] else "created",
                        id=edge["id"],
                        edge=edge,
                    )
                )
        events.append(self._index(now))
        return events

    def changes(self, *, actor, now, role=None, since=None):
        """Observe current files/time and return events plus the latest full cursor.

        Without since, return only this observation's envelopes. A retained
        cursor replays strictly its successors. Invalid, expired, future or
        prior-generation cursors return one consumer-local copy of the current
        retained index with reset, without evicting another consumer's cursor.
        Stored record removals also reset. Returned containers are detached.
        """
        events = self._observe(actor=actor, now=now, role=role)
        if since is not None:
            match = _CURSOR.fullmatch(since) if isinstance(since, str) else None
            retained = list(self._events)
            position = next(
                (i for i, event in enumerate(retained) if event["cursor"] == since),
                None,
            )
            if match and match[1] == self._generation and position is not None:
                events = retained[position + 1 :]
            elif not (len(events) == 1 and events[0].get("reset")):
                events = [dict(self._events[-1], reset=True)]
        return {"events": deepcopy(events), "cursor": self._events[-1]["cursor"]}

    def snapshot(self, *, actor, now, role=None):
        """Return one graph and its associated observation cursor for catch-up.

        Subscribe from this cursor, not a later unrelated observation. A write
        racing the snapshot is either in this graph or observed by catch-up;
        a reset requires fetching a fresh associated snapshot instead.
        """
        result = self.changes(actor=actor, now=now, role=role)
        return {"graph": deepcopy(self._graph), "cursor": result["cursor"]}
