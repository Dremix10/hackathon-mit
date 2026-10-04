"""Simulator package (Workstream A).

Importing this package does not import Omnigent. ``coop.sim.run`` is the CLI.
"""

from coop.sim.env import CoopSim
from coop.sim.violations import detect_violations

__all__ = ["CoopSim", "detect_violations"]
