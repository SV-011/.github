"""No workflow in .github/workflows may trigger on workflow_dispatch or schedule.

Stdlib only; reads the workflow text, no YAML dependency.

Derivation (no invariant names workflow_dispatch, schedule or cron verbatim;
StegVerse-Labs/.github main@149f16e): every action is a manifest-bound
attempted state transition (data/task-registry-global-invariants.json:54
actions_by_manifest_required, :120); external machine awaiting is not a
transition predicate (:58, :160 NO_EXTERNAL_MACHINE_AWAITING_AS_TRANSITION_PREDICATE);
no always-on receiver is required (:60, :161); nothing reads a clock and
nothing mints an epoch (data/sv-work-items/invariants.json:133). A manual
dispatch makes a state transition wait on an operator; a schedule reads a
clock. A writer runs on the committed push that carries its request
(precedent: organization-ledger-merge-transfer.yml:6-8).

There are no exemptions.
"""

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
FORBIDDEN = re.compile(r"^\s*(workflow_dispatch|schedule)\s*:")


def forbidden_triggers():
    found = {}
    for path in sorted(WORKFLOWS.rglob("*.y*ml")):
        rel = str(path.relative_to(ROOT))
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            m = FORBIDDEN.match(line)
            if m:
                found.setdefault((rel, m.group(1)), []).append(lineno)
    return found


class WorkflowTriggers(unittest.TestCase):
    def test_workflows_exist(self):
        self.assertTrue(list(WORKFLOWS.rglob("*.y*ml")), "no workflow files found")

    def test_no_manual_dispatch_or_schedule_trigger(self):
        bad = {f"{rel}:{lines[0]}: {trigger}" for (rel, trigger), lines in forbidden_triggers().items()}
        message = json.dumps(
            {
                "failure_code": "WORKFLOW_TRIGGER_NOT_A_COMMITTED_STATE_TRANSITION",
                "failed_predicate": "no workflow in .github/workflows/** triggers on "
                f"workflow_dispatch or schedule; found: {sorted(bad)}",
                "required_evidence_or_repair": "validation-only: drop the trigger; "
                "writer: trigger on push to main of a committed request file and read "
                "ids and epoch from that file and the ledger head (absent -> "
                "NOT_SUPPLIED, mismatch -> INVALID, no clock)",
                "retry_entrypoint": "python3 -m unittest tests.test_workflow_triggers -v",
                "owning_existing_goal": "SVORG-LLM-ORG-FOUNDATION-001",
                "next_attempt": "re-run the retry_entrypoint after the repair",
            },
            indent=2,
        )
        self.assertEqual(sorted(bad), [], message)

if __name__ == "__main__":
    unittest.main()
