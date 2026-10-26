"""Diagnostic storage profiles, never acceptance measurements or budget retries.

Run with the installed test environment: python -m tests.profile_performance NEW_DIR
Preparation uses the exact strict-gate fixtures. Profiling overhead is included
in these diagnostic timings; only the ordinary tests establish budget compliance.
"""

import argparse
import cProfile
import json
import os
import platform
import pstats
import sys
from pathlib import Path
from time import perf_counter

import yaml

from researchgraph.project import Project
from tests.test_performance import (
    measure_temporal_scrub,
    prepare_fresh_rebuild,
    prepare_temporal_scrub,
)


def profile_workload(label, callback):
    """Report a cumulative profile while preserving the callback's result/error."""
    profiler = cProfile.Profile()
    started = perf_counter()
    succeeded = False
    try:
        result = profiler.runcall(callback)
        succeeded = True
        return result
    finally:
        print(
            json.dumps(
                {
                    "phase": label,
                    "profiled_seconds": perf_counter() - started,
                    "profiling_not_acceptance": True,
                    "completed": succeeded,
                }
            ),
            flush=True,
        )
        pstats.Stats(profiler).strip_dirs().sort_stats("cumulative").print_stats(35)


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory", type=Path, help="new workspace; existing paths are refused"
    )
    options = parser.parse_args(arguments)
    options.directory.mkdir(parents=True)
    print(
        json.dumps(
            {
                "python": sys.version,
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
                "pyyaml": yaml.__version__,
                "libyaml": yaml.__with_libyaml__,
                "pyyaml_path": yaml.__file__,
                "profiling_not_acceptance": True,
            }
        ),
        flush=True,
    )
    project, commits, ordered, disclosure = prepare_temporal_scrub(
        options.directory / "scrub"
    )
    try:
        measurement = profile_workload(
            "200_public_temporal_reads",
            lambda: measure_temporal_scrub(project, commits, ordered),
        )
        print(json.dumps({**disclosure, **measurement}), flush=True)
    finally:
        project.close()
    root, source_bytes, prepared = prepare_fresh_rebuild(options.directory / "rebuild")
    project = profile_workload("20k_fresh_rebuild", lambda: Project.open(root))
    try:
        walk_seconds = project.index.last_history_seconds
        scan_seconds = project.index.last_scan_seconds
        print(
            json.dumps(
                {
                    "nodes": len(project.get_nodes()),
                    "source_bytes": source_bytes,
                    "prepare_seconds": prepared,
                    "git_walk_seconds": walk_seconds,
                    "content_scan_seconds": scan_seconds,
                }
            ),
            flush=True,
        )
    finally:
        project.close()


if __name__ == "__main__":
    main()
