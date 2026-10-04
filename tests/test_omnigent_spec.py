"""The bundle parses and validates on the pinned Omnigent version."""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    from omnigent.policies.builtins.cel import cel_policy
    from omnigent.spec.parser import parse
    from omnigent.spec.validator import validate
except ImportError:  # pragma: no cover - exercised where omnigent is installed
    parse = None
    validate = None
    cel_policy = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(parse is not None, "omnigent 0.16 is not installed")
class OmnigentSpecTests(unittest.TestCase):
    def test_bundle_and_subagents_parse(self) -> None:
        spec = parse(ROOT, expand_env=False)
        self._assert_valid(spec)
        names = sorted(sub.name for sub in spec.sub_agents)
        self.assertEqual(
            names,
            ["analyst", "critic", "experimenter", "literature", "planner", "safety"],
        )
        declared = set(spec.tools.agents)
        self.assertEqual(declared, set(names))

    def _assert_valid(self, spec) -> None:
        result = validate(spec)
        self.assertTrue(result.valid, result.errors)
        if spec.guardrails is not None:
            for policy in spec.guardrails.policies:
                function = policy.function
                if function is None or not str(function.path).endswith("cel_policy"):
                    continue
                cel_policy(expression=function.arguments["expression"])
        for sub in spec.sub_agents:
            self._assert_valid(sub)


if __name__ == "__main__":
    unittest.main()
