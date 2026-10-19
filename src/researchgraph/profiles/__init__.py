"""Public profile contracts and the two registered bundled profiles."""

from researchgraph.model import Write
from researchgraph.profiles.base import EdgeType, Gate, NodeType, Profile, version_tuple
from researchgraph.profiles.research import PROFILE as RESEARCH_PROFILE
from researchgraph.profiles.tasks import PROFILE as TASKS_PROFILE

__all__ = ["EdgeType", "Gate", "NodeType", "Profile", "Write", "version_tuple"]

Profile.register(RESEARCH_PROFILE)
Profile.register(TASKS_PROFILE)
