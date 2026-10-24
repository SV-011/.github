"""Writer-only split of the ledger transition workflow (as StegVerse-Labs/.github#3255).

organization-ledger-transition.yml is PR-facing and read-only: on pull_request
and push its `validate` and `transition` jobs run with contents: read; the
transition job verifies a temporary copy of the published ledger and never
appends or pushes. The self-closure writer is
organization-ledger-transition-writer.yml: COMMITTED_REQUEST (B-P40), run by
the push to main that commits a request under
.stegverse/transition-ledger/requests/, with no dispatch, no input and no
default epoch (tests/test_transition_request.py), job-scoped contents: write,
the shared organization-ledger lock, a fast-forward-only publish and a
fresh-fetch readback. No workflow with a pull_request trigger holds
contents: write. Stdlib only.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
PR_FACING = WORKFLOWS / "organization-ledger-transition.yml"
WRITER = WORKFLOWS / "organization-ledger-transition-writer.yml"
MERGE_WRITER = WORKFLOWS / "organization-ledger-merge-transfer.yml"

WRITER_ENV = """    env:
      LEDGER_REF: refs/stegverse/organization-ledger
      BEFORE: ${{ github.event.before }}
      # bind and verify check the committed request's digest against the file at this revision
      STEGVERSE_COMMITTED_REQUEST_SOURCE: ${{ github.workspace }}#${{ github.sha }}
"""


WRITE_KEY = re.compile(r"(?m)^\s+contents:\s*write\s*$")
WRITE_ALL = re.compile(r"(?m)^\s*permissions:\s*write-all\s*$")


def on_block(text: str) -> str:
    return text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]


def jobs(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    names = re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\n", body)
    parts = re.split(r"(?m)^  [A-Za-z0-9_-]+:\n", body)[1:]
    return dict(zip(names, parts))


def steps(job_text: str) -> list[str]:
    return ["      - name: " + chunk for chunk in job_text.split("\n      - name: ")[1:]]


class WriterSplit(unittest.TestCase):
    def setUp(self):
        self.pr = PR_FACING.read_text(encoding="utf-8")
        self.writer = WRITER.read_text(encoding="utf-8")

    def test_the_pr_facing_workflow_is_read_only_and_has_no_dispatch(self):
        on = on_block(self.pr)
        self.assertEqual(re.findall(r"(?m)^  ([a-z_]+):", on), ["pull_request", "push"])
        self.assertIn("\npermissions:\n  contents: read\n", self.pr)
        self.assertIsNone(WRITE_KEY.search(self.pr))
        self.assertNotIn("git push", self.pr)
        self.assertNotIn("secrets.", self.pr)
        found = jobs(self.pr)
        self.assertEqual(sorted(found), ["transition", "validate"])
        for name, text in found.items():
            self.assertIn("    permissions:\n      contents: read\n", text, name)
            # nothing is conditionally skipped
            self.assertNotRegex(text, r"(?m)^\s+if:", name)

    def test_the_pr_facing_transition_job_verifies_a_temporary_copy(self):
        text = jobs(self.pr)["transition"]
        self.assertEqual(text.count("\n          persist-credentials: false\n"), 2)
        self.assertIn("ref: refs/stegverse/organization-ledger", text)
        self.assertIn("--location \"git+$dry#$LEDGER_REF\"", text)
        for word in ("intr_destination_ingress.py", "STEGVERSE_ORG_LEDGER_ROOT"):
            self.assertNotIn(word, text)
        self.assertIn("group: organization-ledger-verify-${{ github.run_id }}", self.pr)

    def test_the_writer_runs_on_a_committed_request_with_job_scoped_write(self):
        on = on_block(self.writer)
        self.assertEqual(re.findall(r"(?m)^  ([a-z_]+):", on), ["push"])
        self.assertNotIn("inputs", on)
        self.assertIn("\npermissions:\n  contents: read\n", self.writer)
        self.assertIn("concurrency:\n  group: organization-ledger\n  cancel-in-progress: false\n", self.writer)
        found = jobs(self.writer)
        self.assertEqual(list(found), ["transition"])
        job = found["transition"]
        self.assertTrue(job.startswith("    runs-on: ubuntu-latest\n    permissions:\n      contents: write\n"
                                       + WRITER_ENV), job[:300])

    def test_the_writer_appends_publishes_fast_forward_and_reads_back(self):
        names = [step.split("\n", 1)[0] for step in steps(jobs(self.writer)["transition"])]
        self.assertEqual(names, [
            "      - name: Checkout organization boundary",
            "      - name: Bind the designated organization ledger ref",
            "      - name: Read the committed request",
            "      - name: Verify the committed request before any write",
            "      - name: Append the intr-destination diagnostic self-closure",
            "      - name: Bind the committed request to the closure's receipts",
            "      - name: Publish the ledger ref (fast-forward only)",
            "      - name: Read the organization and repository ledgers back from a fresh fetch",
            "      - name: Retain the run's evidence",
        ])
        self.assertIn('git push origin "$LEDGER_REF:$LEDGER_REF"', self.writer)
        self.assertNotRegex(self.writer, r"git push[^\n]*\+")
        self.assertIn("intr_destination_ingress.py --self-closure", self.writer)
        self.assertIn("committed_request_binding.py bind", self.writer)
        self.assertIn("committed_request_binding.py seal --published-tip", self.writer)
        self.assertIn("committed_request_binding.py verify", self.writer)
        self.assertIn("organization_ledger_readback.py \\\n            --location \"git+$fresh#$LEDGER_REF\"",
                      self.writer)

    def test_no_workflow_with_a_pull_request_trigger_holds_contents_write(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if re.search(r"(?m)^  pull_request(_target)?:", on_block(text)):
                self.assertIsNone(WRITE_KEY.search(text), path.name)
                self.assertIsNone(WRITE_ALL.search(text), path.name)
        # the two writers are the only contents: write holders, both job-scoped, neither on pull_request
        holders = sorted(p.name for p in WORKFLOWS.glob("*.yml") if WRITE_KEY.search(p.read_text()))
        self.assertEqual(holders, sorted([WRITER.name, MERGE_WRITER.name]))
        for path in (WRITER, MERGE_WRITER):
            self.assertIn("\npermissions:\n  contents: read\n", path.read_text())


if __name__ == "__main__":
    unittest.main()
