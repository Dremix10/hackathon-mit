"""Family labels are not applied inside the simulator.

``CoopSim`` observations do not carry a hide/show/swap roster. Callers that
still import ``display_labels`` get an empty map, which is the hidden case.
"""

from __future__ import annotations


def display_labels(*_args: object, **_kwargs: object) -> dict[str, str]:
    return {}
