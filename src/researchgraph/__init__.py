"""researchgraph: an append-only research graph in git, for humans and agents.

See spec/ for the normative data model and operations, and tasks/v0.1.md for
the ordered implementation and verification milestones.
"""

from researchgraph.model import Actor, RuleViolation

__version__ = "0.0.1"

__all__ = ["Actor", "Project", "RuleViolation", "__version__"]


def __getattr__(name):
    # Isolated byte-hashing modules must not initialize schema, profile or CLI
    # dependencies merely by importing their enclosing package.
    if name == "Project":
        from researchgraph.project import Project

        globals()[name] = Project
        return Project
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
