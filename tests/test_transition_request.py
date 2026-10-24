"""COMMITTED_REQUEST: the ledger transition writer reads a committed request, not a dispatch.

resident-runtime/transition_request.py gates the request a push to main
commits under .stegverse/transition-ledger/requests/: absent -> NOT_SUPPLIED,
contradicted -> INVALID, no default epoch, no clock. An accepted request's
digest is recorded on the self-closure's repository receipt. Stdlib only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRITER = ROOT / ".github/workflows/organization-ledger-transition-writer.yml"
SIX_FIELDS = ("failure_code", "failed_predicate", "required_evidence_or_repair",
              "retry_entrypoint", "owning_existing_goal", "next_attempt")


def _module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tr = _module("transition_request_under_test", "resident-runtime/transition_request.py")
ingress = _module("intr_destination_ingress_for_request", "resident-runtime/intr_destination_ingress.py")
REGISTRY = json.loads((ROOT / "org-boundary/registry/services.json").read_text(encoding="utf-8"))


def request(**overrides):
    body = {"schema": tr.SCHEMA, "communication_id": "comm-1", "packet_id": "packet-1", "hb_epoch": 33}
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


def raw(value):
    return json.dumps(value).encode()


class Gate(unittest.TestCase):
    def assert_refused(self, result, disposition, predicate):
        self.assertEqual(result["disposition"], disposition, result)
        self.assertEqual(result["failed_predicate"], predicate, result)
        for field in SIX_FIELDS:
            self.assertTrue(result.get(field), field)

    def test_a_complete_request_is_allowed_with_its_digest(self):
        data = raw(request())
        result = tr.gate(data, "r.json", None)
        self.assertEqual(result["disposition"], "ALLOW")
        self.assertEqual((result["communication_id"], result["packet_id"], result["hb_epoch"]),
                         ("comm-1", "packet-1", 33))
        self.assertRegex(result["request_sha256"], r"^sha256:[0-9a-f]{64}$")

    def test_each_missing_field_is_not_supplied_and_there_is_no_default_epoch(self):
        for name in tr.FIELDS:
            result = tr.gate(raw(request(**{name: None})), "r.json", None)
            self.assertEqual(result["disposition"], "NOT_SUPPLIED", name)
            self.assertEqual(result["failure_code"], tr.FIELD_NOT_SUPPLIED)
            self.assertIn(name, result["detail"])
            for field in SIX_FIELDS:
                self.assertTrue(result.get(field), field)

    def test_contradicted_requests_are_invalid(self):
        cases = [
            (b"not json", "REQUEST_IS_ONE_JSON_OBJECT"),
            (raw([1]), "REQUEST_IS_ONE_JSON_OBJECT"),
            (raw(request(schema="other/v1")), "REQUEST_DECLARES_THE_SCHEMA"),
            (raw(request(communication_id="")), "COMMUNICATION_ID_IS_A_PRINTABLE_TOKEN"),
            (raw(request(packet_id="a\nb")), "PACKET_ID_IS_A_PRINTABLE_TOKEN"),
            (raw(request(hb_epoch=31)), "HB_EPOCH_IS_A_SUPPLIED_INT_AT_LEAST_32"),
            (raw(request(hb_epoch="33")), "HB_EPOCH_IS_A_SUPPLIED_INT_AT_LEAST_32"),
            (raw(request(hb_epoch=True)), "HB_EPOCH_IS_A_SUPPLIED_INT_AT_LEAST_32"),
            (raw(request(hb_epoch=33.0)), "HB_EPOCH_IS_A_SUPPLIED_INT_AT_LEAST_32"),
        ]
        for data, predicate in cases:
            self.assert_refused(tr.gate(data, "r.json", None), "INVALID", predicate)

    def test_an_epoch_before_the_ledger_head_is_invalid(self):
        self.assert_refused(tr.gate(raw(request(hb_epoch=33)), "r.json", 34), "INVALID",
                            "HB_EPOCH_NOT_BEFORE_THE_LEDGER_HEAD")
        self.assertEqual(tr.gate(raw(request(hb_epoch=34)), "r.json", 34)["disposition"], "ALLOW")


class PushSelection(unittest.TestCase):
    def setUp(self):
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        self.repo = Path(self._work.name)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        (self.repo / "README").write_text("x")
        self.base = self.commit()
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)

    def git(self, *args):
        return subprocess.run(("git", "-C", str(self.repo)) + args, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        return self.git("rev-parse", "HEAD")

    def write(self, name, value):
        path = self.repo / tr.REQUEST_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw(value))

    def test_the_committed_request_is_read_from_the_push(self):
        self.write("a.json", request())
        after = self.commit()
        result = tr.select(self.base, after, None)
        self.assertEqual(result["disposition"], "ALLOW", result)
        self.assertEqual(result["request_path"], tr.REQUEST_DIR + "/a.json")

    def test_a_push_without_a_request_writes_nothing(self):
        (self.repo / "other").write_text("y")
        self.assertEqual(tr.select(self.base, self.commit(), None)["disposition"], tr.NO_REQUEST)

    def test_one_request_per_push(self):
        self.write("a.json", request())
        self.write("b.json", request(packet_id="packet-2"))
        result = tr.select(self.base, self.commit(), None)
        self.assertEqual((result["disposition"], result["failed_predicate"]), ("INVALID", "ONE_REQUEST_PER_PUSH"))

    def test_a_first_push_reads_the_root_commit(self):
        self.write("a.json", request())
        after = self.commit()
        self.assertEqual(tr.select("0" * 40, after, None)["disposition"], "ALLOW")


class RequestDigestOnTheReceipt(unittest.TestCase):
    def setUp(self):
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        for name in ("STEGVERSE_REPO_LEDGER_ROOT", "STEGVERSE_ORG_LEDGER_ROOT"):
            previous = os.environ.get(name)
            os.environ[name] = str(Path(self._work.name) / name.lower())
            self.addCleanup(lambda n=name, v=previous: os.environ.pop(n, None) if v is None
                            else os.environ.__setitem__(n, v))

    def test_the_repository_receipt_carries_the_request_digest(self):
        digest = "sha256:" + "a" * 64
        result = ingress.self_closure("comm-1", registry=REGISTRY, packet_id="packet-digest",
                                      hb_epoch=33, request_sha256=digest)
        self.assertEqual(result["disposition"], "ALLOW", result)
        found = [json.loads(p.read_text()) for p in Path(os.environ["STEGVERSE_REPO_LEDGER_ROOT"]).rglob("*.json")]
        receipts = [r for r in found if r.get("schema") == "stegverse.repo-transition-receipt/v1"]
        self.assertEqual([r["evidence"]["request_sha256"] for r in receipts], [digest])

    def test_a_closure_without_a_request_is_unchanged(self):
        self.assertNotIn("request_sha256", ingress.diagnostic_payload("comm-1")["body"])


class WriterTrigger(unittest.TestCase):
    def test_the_writer_runs_on_a_committed_request_and_takes_no_input(self):
        text = WRITER.read_text(encoding="utf-8")
        on = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(on.strip(), "push:\n    branches: [main]\n    paths:\n"
                                     "      - '.stegverse/transition-ledger/requests/**'")
        for word in ("workflow_dispatch", "schedule", "cron", "inputs", "default:"):
            self.assertNotIn(word, text.split("\njobs:", 1)[0].split("\non:\n", 1)[1], word)
        self.assertNotIn("inputs.", text)
        self.assertIn("resident-runtime/transition_request.py --before \"$BEFORE\" --after \"$GITHUB_SHA\"", text)
        self.assertNotRegex(text, r"(?i)\bdate\b|now\(|time\.time")


if __name__ == "__main__":
    unittest.main()
