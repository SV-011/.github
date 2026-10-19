"""The ledger transition job runs on every event; only workflow_dispatch appends.

The PR-safe transition variant (as StegVerse-Labs/.github#3251): on
pull_request and push the `transition` job runs a verify against a temporary
copy of the published ledger instead of being skipped. Every step that binds,
appends, publishes or reads back the real ledger is gated on workflow_dispatch,
unchanged from before. Stdlib only.
"""
from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/organization-ledger-transition.yml"
DISPATCH = "github.event_name == 'workflow_dispatch'"
VERIFY = "github.event_name != 'workflow_dispatch'"


def job(text: str, name: str) -> str:
    body = text.split("\n  " + name + ":\n", 1)[1]
    end = re.search(r"\n  [A-Za-z0-9_-]+:\n", body)
    return body[: end.start()] if end else body


def steps(job_text: str) -> list[str]:
    return ["      - name: " + chunk for chunk in job_text.split("\n      - name: ")[1:]]


class TransitionJobRunsOnEveryEvent(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.transition = job(self.text, "transition")

    def test_the_job_has_no_job_level_condition(self):
        header = self.transition.split("    steps:\n", 1)[0]
        self.assertNotRegex(header, r"(?m)^    if:")

    def test_every_step_is_gated_to_dispatch_or_verify(self):
        kinds = []
        for step in steps(self.transition):
            gate = re.search(r"(?m)^        if: (.+)$", step)
            self.assertIsNotNone(gate, step.splitlines()[0])
            kinds.append(gate.group(1))
        self.assertEqual(kinds[:3], [VERIFY] * 3)
        self.assertEqual(kinds[-1], "always()")
        self.assertTrue(all(k == DISPATCH for k in kinds[3:-1]))
        self.assertGreaterEqual(kinds.count(DISPATCH), 5)

    def test_verify_steps_hold_no_credential_and_never_push(self):
        verify = [s for s in steps(self.transition) if "        if: " + VERIFY in s]
        joined = "\n".join(verify)
        self.assertEqual(joined.count("\n          persist-credentials: false\n"), 2)
        self.assertIn("ref: refs/stegverse/organization-ledger", joined)
        for word in ("git push", "intr_destination_ingress.py", "secrets.", "STEGVERSE_ORG_LEDGER_ROOT"):
            self.assertNotIn(word, joined)
        self.assertIn("--location \"git+$dry#$LEDGER_REF\"", joined)

    def test_dispatch_steps_are_unchanged_from_main(self):
        # sha256 of the transition job's steps at main d7beaf59 (each step right-stripped, joined by newline);
        # pinned, so a shallow CI checkout needs no history
        now = [s for s in steps(self.transition) if "        if: " + VERIFY not in s]
        strip = lambda s: s.replace("        if: " + DISPATCH + "\n", "").rstrip()
        self.assertEqual(hashlib.sha256("\n".join(strip(s) for s in now).encode()).hexdigest(),
                         "a632cbb00fe774dff6e278fc9676b62b0e79e0dd582f48e5ae38e97bb70771a6")

    def test_a_verify_run_does_not_take_the_appender_lock(self):
        self.assertIn("group: organization-ledger${{ github.event_name != 'workflow_dispatch' && "
                      "format('-verify-{0}', github.run_id) || '' }}", self.text)


if __name__ == "__main__":
    unittest.main()
