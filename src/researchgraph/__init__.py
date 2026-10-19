"""researchgraph: an append-only research graph in git, for humans and agents.

See spec/ for the normative data model and operations, and tasks/v0.1.md for
the ordered implementation and verification milestones.
"""

from researchgraph.model import Actor, RuleViolation
from researchgraph.project import Project

__version__ = "0.0.1"

__all__ = ["Actor", "Project", "RuleViolation", "__version__"]
