"""Every `uses:` in .github/workflows must be pinned to a 40-hex commit sha.

Stdlib only. Local actions (`./...`) are exempt; the tag may be kept as a
trailing comment, e.g. `actions/checkout@<sha> # v4`.
"""

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^\s'\"#]+)")
PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def unpinned_uses():
    found = []
    for path in sorted(WORKFLOWS.rglob("*.y*ml")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            m = USES.match(line)
            if not m:
                continue
            ref = m.group(1)
            if ref.startswith("./") or PINNED.match(ref):
                continue
            found.append(f"{path.relative_to(ROOT)}:{lineno}: {ref}")
    return found


class WorkflowActionPins(unittest.TestCase):
    def test_workflows_exist(self):
        self.assertTrue(list(WORKFLOWS.rglob("*.y*ml")), "no workflow files found")

    def test_every_uses_is_pinned_to_a_full_sha(self):
        bad = unpinned_uses()
        message = json.dumps(
            {
                "failure_code": "WORKFLOW_ACTION_NOT_PINNED",
                "failed_predicate": "every `uses:` in .github/workflows/** matches "
                "<owner>/<action>@<40-hex sha> (local ./ paths excepted); "
                f"unpinned: {bad}",
                "required_evidence_or_repair": "pin each listed `uses:` to the commit "
                "sha resolved by `git ls-remote https://github.com/<owner>/<action> "
                "refs/tags/<tag>` (peel annotated tags with ^{}), keeping the tag as "
                "a trailing comment",
                "retry_entrypoint": "python3 -m unittest tests.test_workflow_action_pins -v",
                "owning_existing_goal": "SVORG-LLM-ORG-FOUNDATION-001",
                "next_attempt": "re-run the retry_entrypoint after pinning; CI re-runs "
                "on the next push touching .github/workflows/**",
            },
            indent=2,
        )
        self.assertEqual(bad, [], message)


if __name__ == "__main__":
    unittest.main()
