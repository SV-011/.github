"""Writer-only split of the ledger transition workflow (as StegVerse-Labs/.github#3255).

organization-ledger-transition.yml is PR-facing and read-only: on pull_request
and push its `validate` and `transition` jobs run with contents: read; the
transition job verifies a temporary copy of the published ledger and never
appends or pushes. The workflow_dispatch self-closure writer is
organization-ledger-transition-writer.yml: dispatch only, job-scoped
contents: write, the shared organization-ledger lock, and steps byte-identical
to the dispatch job at main d7beaf59 (pinned by digest, so a shallow CI
checkout needs no history). No workflow with a pull_request trigger holds
contents: write. Stdlib only.
"""
from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
PR_FACING = WORKFLOWS / "organization-ledger-transition.yml"
WRITER = WORKFLOWS / "organization-ledger-transition-writer.yml"
MERGE_WRITER = WORKFLOWS / "organization-ledger-merge-transfer.yml"
#: the InTr path writers (StegVerse-org/.github#162): workflow_run on the main-merge transfer only.
INTR_WRITERS = (WORKFLOWS / "intr-path-transfer.yml", WORKFLOWS / "intr-path-ingress.yml")

#: sha256 of the dispatch job's steps at main d7beaf59 (each step right-stripped, joined by newline,
#: the job-level and step-level dispatch conditions removed).
MAIN_DISPATCH_STEPS_SHA256 = "a632cbb00fe774dff6e278fc9676b62b0e79e0dd582f48e5ae38e97bb70771a6"
MAIN_DISPATCH_BLOCK = """  workflow_dispatch:
    inputs:
      communication_id:
        description: 'communication_id the closure correlates on'
        required: true
        type: string
      packet_id:
        description: 'packet id for this diagnostic self-closure'
        required: true
        type: string
      hb_epoch:
        description: 'heartbeat epoch constraint (integer, at least 32)'
        required: true
        default: '32'
        type: string
"""
MAIN_DISPATCH_ENV = """    env:
      LEDGER_REF: refs/stegverse/organization-ledger
      STEGVERSE_COMMUNICATION_ID: ${{ inputs.communication_id }}
      STEGVERSE_PACKET_ID: ${{ inputs.packet_id }}
      STEGVERSE_HB_EPOCH: ${{ inputs.hb_epoch }}
"""


#: action pins (M-P9) mapped back to the tags main d7beaf59 used, so the digest still binds every other byte.
ACTION_PINS = {
    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4": "actions/checkout@v4",
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4": "actions/upload-artifact@v4",
}


def unpin(step: str) -> str:
    for pinned, tag in ACTION_PINS.items():
        step = step.replace(pinned, tag)
    return step


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

    def test_the_writer_is_dispatch_only_with_job_scoped_write(self):
        on = on_block(self.writer)
        self.assertEqual(re.findall(r"(?m)^  ([a-z_]+):", on), ["workflow_dispatch"])
        self.assertEqual(on.strip("\n") + "\n", MAIN_DISPATCH_BLOCK)
        self.assertIn("\npermissions:\n  contents: read\n", self.writer)
        self.assertIn("concurrency:\n  group: organization-ledger\n  cancel-in-progress: false\n", self.writer)
        found = jobs(self.writer)
        self.assertEqual(list(found), ["transition"])
        job = found["transition"]
        self.assertTrue(job.startswith("    runs-on: ubuntu-latest\n    permissions:\n      contents: write\n"
                                       + MAIN_DISPATCH_ENV), job[:300])

    def test_the_writer_steps_are_the_dispatch_steps_of_main(self):
        now = [unpin(s).rstrip() for s in steps(jobs(self.writer)["transition"])]
        self.assertEqual(hashlib.sha256("\n".join(now).encode()).hexdigest(), MAIN_DISPATCH_STEPS_SHA256)

    def test_no_workflow_with_a_pull_request_trigger_holds_contents_write(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if re.search(r"(?m)^  pull_request(_target)?:", on_block(text)):
                self.assertIsNone(WRITE_KEY.search(text), path.name)
                self.assertIsNone(WRITE_ALL.search(text), path.name)
        # the ledger writers are the only contents: write holders, all job-scoped, none on pull_request
        holders = sorted(p.name for p in WORKFLOWS.glob("*.yml") if WRITE_KEY.search(p.read_text()))
        self.assertEqual(holders, sorted([WRITER.name, MERGE_WRITER.name] + [p.name for p in INTR_WRITERS]))
        for path in (WRITER, MERGE_WRITER) + INTR_WRITERS:
            self.assertIn("\npermissions:\n  contents: read\n", path.read_text())
        for path in INTR_WRITERS:
            text = path.read_text()
            self.assertEqual(re.findall(r"(?m)^  ([a-z_]+):", on_block(text)), ["workflow_run"], path.name)
            self.assertIn('workflows: ["Organization Ledger Main Merge Transfer"]', text, path.name)
            self.assertIn("concurrency:\n  group: organization-ledger\n  cancel-in-progress: false\n", text, path.name)


if __name__ == "__main__":
    unittest.main()
