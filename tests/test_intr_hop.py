"""One-way InTr hop: send, receive, close, each in its own process.

The destination is a temporary copy of this checkout whose advertisement is
re-stamped as ``Hop-Peer-Fixture``. Ledgers are temporary git refs. Every
receipt here is test evidence, fabricated and non-authentic. Nothing is
published.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = "refs/stegverse/organization-ledger"
FIXTURE = "Hop-Peer-Fixture"
FIXTURE_SLUG = "hop-peer-fixture"
STAMPED = (
    "org-boundary/registry/services.json",
    "org-runtime/interlock-intr.json",
    ".stegverse/transition-ledger/contract.json",
    ".stegverse/transition-ledger/org-contract.json",
)


def registry(root: Path) -> dict:
    return json.loads((root / "org-boundary/registry/services.json").read_text(encoding="utf-8"))


def slug(organization: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in organization).strip("-")


def namespace(root: Path) -> str:
    contract = json.loads((root / ".stegverse/transition-ledger/contract.json").read_text(encoding="utf-8"))
    return contract["repository_ledger_location"]["namespace"]


def stamp_fixture(dest: Path) -> Path:
    organization = registry(ROOT)["organization"]
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    for relative in STAMPED:
        path = dest / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        text = text.replace(organization, FIXTURE).replace(slug(organization) + ".", FIXTURE_SLUG + ".")
        path.write_text(text, encoding="utf-8")
    return dest


class Side:
    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        self.ledger = home / "ledger"
        subprocess.run(["git", "init", "-q", str(self.ledger)], check=True)
        for key, value in (("user.email", "hop-fixture@example.invalid"), ("user.name", "hop-fixture")):
            subprocess.run(["git", "-C", str(self.ledger), "config", key, value], check=True)

    def env(self) -> dict:
        env = dict(os.environ)
        for key in list(env):
            if key.startswith("STEGVERSE_"):
                env.pop(key)
        env.update({
            "STEGVERSE_ORG_LEDGER_ROOT": "git+%s#%s" % (self.ledger, REF),
            "STEGVERSE_ORG_LEDGER_GIT_DIR": str(self.ledger / ".git"),
            "STEGVERSE_REPO_LEDGER_ROOT": "git+%s#%s:%s" % (self.ledger, REF, namespace(self.root)),
        })
        return env

    def run(self, *args: str) -> tuple[int, dict]:
        out = Path(tempfile.mkdtemp()) / "result.json"
        completed = subprocess.run(
            [sys.executable, str(self.root / "resident-runtime/intr_hop.py"), *args, "--out", str(out)],
            cwd=self.root, env=self.env(), capture_output=True, text=True, timeout=180)
        if not out.is_file():
            raise AssertionError(completed.stderr[-2000:])
        return completed.returncode, json.loads(out.read_text(encoding="utf-8"))

    def chain(self, repository: bool) -> list[dict]:
        prefix = namespace(self.root) + "/" if repository else ""

        def blob(key: str):
            completed = subprocess.run(["git", "-C", str(self.ledger), "cat-file", "blob", REF + ":" + prefix + key],
                                       capture_output=True)
            return json.loads(completed.stdout) if completed.returncode == 0 else None

        head = blob("HEAD.json")
        cursor = head and head.get("receipt_sha256")
        receipts = []
        while cursor:
            receipt = blob("receipts/" + cursor.split(":", 1)[1] + ".json")
            receipts.append(receipt)
            cursor = receipt.get("previous_receipt_sha256")
        return list(reversed(receipts))


NON_ALLOW_FIELDS = ("failure_code", "failed_predicate", "required_evidence_or_repair",
                    "retry_entrypoint", "owning_existing_goal", "next_attempt")
OWNING_GOAL = "StegVerse-Labs/.github#2897 / StegVerse-org/.github#162 P0-runtime-and-custody"


def assert_six_fields(test: unittest.TestCase, result: dict) -> None:
    test.assertNotEqual(result.get("disposition"), "ALLOW", result)
    for field in NON_ALLOW_FIELDS:
        test.assertIsInstance(result.get(field), str, (field, result))
        test.assertTrue(result[field].strip(), (field, result))
    test.assertEqual(result["owning_existing_goal"], OWNING_GOAL)


def frame_file(text: str) -> str:
    path = Path(tempfile.mkdtemp()) / "frame.b64"
    path.write_text(text, encoding="ascii")
    return str(path)


class OneWayHopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.home = Path(tempfile.mkdtemp(prefix="intr-hop-test-"))
        cls.peer_root = stamp_fixture(cls.home / "peer")
        cls.organization = registry(ROOT)["organization"]
        cls.slug = slug(cls.organization)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.home, ignore_errors=True)

    def sides(self, name: str) -> tuple[Side, Side]:
        base = self.home / name
        return Side(ROOT, base / "sender"), Side(self.peer_root, base / "destination")

    def hop(self, sender: Side, destination: Side, destination_slug: str, origin_slug: str,
            communication_id: str, epoch: int):
        code, sent = sender.run("send", "--destination-slug", destination_slug,
                                "--communication-id", communication_id, "--hb-epoch", str(epoch),
                                "--peer-root", str(destination.root))
        self.assertEqual(code, 0, sent)
        code, arrived = destination.run("receive", "--frame-file", frame_file(sent["frame"]),
                                        "--origin-slug", origin_slug, "--hb-epoch", str(epoch + 1))
        self.assertEqual(code, 0, arrived)
        code, closed = sender.run("close", "--frame-file", frame_file(arrived["frame"]),
                                  "--destination-slug", destination_slug,
                                  "--communication-id", communication_id, "--hb-epoch", str(epoch + 2))
        self.assertEqual(code, 0, closed)
        return sent, arrived, closed

    def test_sent_arrived_closed_share_the_communication_id(self) -> None:
        sender, destination = self.sides("forward")
        cid = "intr-hop:%s:%s:test" % (self.slug, FIXTURE_SLUG)
        sent, arrived, closed = self.hop(sender, destination, FIXTURE_SLUG, self.slug, cid, 4000)
        self.assertEqual(sent["destination_service"], FIXTURE_SLUG + ".intr-destination")
        self.assertIs(sent["federation_directory_read"], False)
        self.assertEqual(arrived["response_message_class"], "ecosystem.communication.ack")
        self.assertTrue(closed["closed"], closed)
        self.assertTrue(closed["peer_serves_this_capability_is_proven_here"])

        repo = sender.chain(repository=True)
        self.assertEqual([item["transition_class"] for item in repo],
                         ["ORGANIZATION_EGRESS_EMITTED", "ORGANIZATION_EGRESS_CLOSED"])
        emitted, closure = repo
        self.assertEqual(closure["evidence"]["communication_id"], cid)
        self.assertEqual(emitted["evidence"]["packet_id"], sent["packet_id"])
        self.assertEqual(emitted["evidence"]["destination_organization"], FIXTURE)
        self.assertEqual(emitted["evidence"]["destination_service"], FIXTURE_SLUG + ".intr-destination")
        self.assertEqual(closure["evidence"]["request_packet_id"], emitted["evidence"]["packet_id"])
        self.assertEqual(closure["evidence"]["response_message_class"], "ecosystem.communication.ack")
        self.assertEqual(closure["evidence"]["disposition"], "ALLOW")
        self.assertEqual([item["hb_reference"]["epoch"] for item in repo], [4000, 4002])
        self.assertFalse(any((item.get("hb_reference") or {}).get("derived_from_clock") for item in repo))
        held = {item["repo_receipt_sha256"] for item in sender.chain(repository=False)}
        self.assertEqual(held, {item["receipt_sha256"] for item in repo})

        for side, result in ((sender, sent), (destination, arrived), (sender, closed)):
            result_path = Path(tempfile.mkdtemp()) / "phase.json"
            result_path.write_text(json.dumps(result), encoding="utf-8")
            code, read = side.run("readback", "--location", "git+%s#%s" % (side.ledger, REF),
                                  "--repository-namespace", namespace(side.root), "--result", str(result_path))
            self.assertEqual(code, 0, read)
            self.assertEqual(read["failed_predicates"], [])

        far = destination.chain(repository=True)
        self.assertEqual([(item["transition_class"], item["evidence"].get("communication_id")) for item in far],
                         [("ORGANIZATION_INTR_DESTINATION_INGRESS", cid)])
        self.assertEqual(far[0]["evidence"]["profile_id"], "intr-destination")
        self.assertEqual(far[0]["evidence"]["disposition"], "ALLOW")
        self.assertEqual(far[0]["hb_reference"]["epoch"], 4001)
        self.assertEqual({item["repo_receipt_sha256"] for item in destination.chain(repository=False)},
                         {far[0]["receipt_sha256"]})

    def test_reverse_hop_runs_on_the_destination_code(self) -> None:
        sender, destination = self.sides("reverse")
        cid = "intr-hop:%s:%s:test" % (FIXTURE_SLUG, self.slug)
        _, _, closed = self.hop(destination, sender, self.slug, FIXTURE_SLUG, cid, 5000)
        self.assertTrue(closed["closed"], closed)
        self.assertEqual([item["transition_class"] for item in sender.chain(repository=True)],
                         ["ORGANIZATION_INTR_DESTINATION_INGRESS"])

    def test_replayed_phases_return_the_recorded_receipts(self) -> None:
        sender, destination = self.sides("replay")
        cid = "intr-hop:%s:%s:replay" % (self.slug, FIXTURE_SLUG)
        first = self.hop(sender, destination, FIXTURE_SLUG, self.slug, cid, 6000)
        second = self.hop(sender, destination, FIXTURE_SLUG, self.slug, cid, 6000)
        for a, b in zip(first, second):
            self.assertEqual(a["repository_receipt_sha256"], b["repository_receipt_sha256"])
        self.assertEqual(len(sender.chain(repository=True)), 2)
        self.assertEqual(len(destination.chain(repository=True)), 1)

    def test_refusals_append_nothing(self) -> None:
        sender, destination = self.sides("refusals")
        cases = (
            ("send", "--destination-slug", self.slug, "--communication-id", "c", "--hb-epoch", "1",
             "--peer-root", str(ROOT)),
            ("send", "--destination-slug", "not-the-advertiser", "--communication-id", "c", "--hb-epoch", "1",
             "--peer-root", str(destination.root)),
            ("send", "--destination-slug", FIXTURE_SLUG, "--communication-id", "has space", "--hb-epoch", "1",
             "--peer-root", str(destination.root)),
            ("send", "--destination-slug", FIXTURE_SLUG, "--communication-id", "c", "--hb-epoch", "-1",
             "--peer-root", str(destination.root)),
        )
        for args in cases:
            code, result = sender.run(*args)
            self.assertNotEqual(code, 0, args)
            self.assertEqual(result["disposition"], "FAIL_CLOSED", result)
            self.assertIs(result["receipts_appended"], False)
            assert_six_fields(self, result)
        self.assertEqual(sender.chain(repository=True), [])

    def test_answer_for_another_communication_is_not_a_closure(self) -> None:
        sender, destination = self.sides("mismatch")
        cid = "intr-hop:%s:%s:mismatch" % (self.slug, FIXTURE_SLUG)
        code, sent = sender.run("send", "--destination-slug", FIXTURE_SLUG, "--communication-id", cid,
                                "--hb-epoch", "7000", "--peer-root", str(destination.root))
        self.assertEqual(code, 0, sent)
        code, arrived = destination.run("receive", "--frame-file", frame_file(sent["frame"]),
                                        "--origin-slug", "someone-else", "--hb-epoch", "7001")
        self.assertNotEqual(code, 0)
        self.assertEqual(arrived["failed_predicate"], "FRAME_ORIGIN_IS_THE_DISPATCHING_PEER")
        assert_six_fields(self, arrived)
        code, arrived = destination.run("receive", "--frame-file", frame_file(sent["frame"]),
                                        "--origin-slug", self.slug, "--hb-epoch", "7001")
        self.assertEqual(code, 0, arrived)
        code, closed = sender.run("close", "--frame-file", frame_file(arrived["frame"]),
                                  "--destination-slug", FIXTURE_SLUG, "--communication-id", cid + "-other",
                                  "--hb-epoch", "7002")
        self.assertNotEqual(code, 0)
        self.assertEqual(closed["failed_predicate"], "ANSWER_CARRIES_THE_HOP_COMMUNICATION_ID")
        assert_six_fields(self, closed)
        self.assertEqual([item["transition_class"] for item in sender.chain(repository=True)],
                         ["ORGANIZATION_EGRESS_EMITTED"])

    def test_readback_without_receipts_carries_the_six_fields(self) -> None:
        sender, _ = self.sides("readback-empty")
        result_path = Path(tempfile.mkdtemp()) / "phase.json"
        result_path.write_text(json.dumps({"phase": "send", "communication_id": "c", "hb_epoch": 1,
                                           "repository_receipt_sha256": "sha256:" + "0" * 64}), encoding="utf-8")
        subprocess.run(["git", "-C", str(sender.ledger), "commit", "-q", "--allow-empty", "-m", "seed"], check=True)
        subprocess.run(["git", "-C", str(sender.ledger), "update-ref", REF, "HEAD"], check=True)
        code, read = sender.run("readback", "--location", "git+%s#%s" % (sender.ledger, REF),
                                "--repository-namespace", namespace(sender.root), "--result", str(result_path))
        self.assertNotEqual(code, 0)
        self.assertEqual(read["disposition"], "FAIL_CLOSED")
        assert_six_fields(self, read)

    def test_refused_ingress_and_closure_carry_the_six_fields(self) -> None:
        sys.path.insert(0, str(ROOT / "resident-runtime"))
        try:
            import intr_hop
        finally:
            sys.path.pop(0)
        refused = intr_hop._closure_refused(["FAR_SIDE_INTR_DESTINATION_ACKNOWLEDGED_THIS_COMMUNICATION"])
        assert_six_fields(self, {"disposition": "DENY", **refused})
        self.assertEqual(refused["failed_predicate"], "FAR_SIDE_INTR_DESTINATION_ACKNOWLEDGED_THIS_COMMUNICATION")
        assert_six_fields(self, {"disposition": "DENY", **intr_hop._closure_refused([])})

    def test_workflow_non_allow_outcomes_carry_the_six_fields(self) -> None:
        workflow = (ROOT / ".github/workflows/intr-destination-hop.yml").read_text(encoding="utf-8")
        lines = [line for line in workflow.splitlines() if '"disposition":"FAIL_CLOSED"' in line
                 or '"disposition": "FAIL_CLOSED"' in line]
        self.assertGreaterEqual(len(lines), 4)
        for line in lines:
            if line.strip().startswith("result = {"):
                start = workflow.index(line)
                line = workflow[start:workflow.index("}", start)]
            for field in NON_ALLOW_FIELDS:
                self.assertIn('"%s"' % field, line, (field, line[:120]))
        self.assertIn(OWNING_GOAL, workflow)
        code = "\n".join(line for line in workflow.splitlines() if not line.lstrip().startswith("#"))
        for waiting in ("sleep ", "poll", "wave_timeout", "gh run watch"):
            self.assertNotIn(waiting, code)

    def test_the_hop_does_not_read_the_federation_directory(self) -> None:
        source = (ROOT / "resident-runtime/intr_hop.py").read_text(encoding="utf-8")
        self.assertNotIn("federation.json", source.replace("``federation.json`` is not read", ""))
        self.assertNotIn("load_federation_directory", source)
        self.assertNotIn("resolve_destination(", source)


if __name__ == "__main__":
    unittest.main()
