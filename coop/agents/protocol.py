"""Canonical protocol text lives in ``coop.sim.protocol``.

This module only re-exports it so an older import does not shadow the simulator.
"""

from coop.sim.protocol import CHARTER_TEXT, banned_hits, template_map_for

__all__ = ["CHARTER_TEXT", "banned_hits", "template_map_for"]
