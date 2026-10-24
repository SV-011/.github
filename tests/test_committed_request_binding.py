"""The committed request is bound to every receipt its self-closure produced.

The InTr ingress writes the repository and organization receipts unchanged;
resident-runtime/committed_request_binding.py appends one companion
stegverse.committed-request-binding-receipt/v1 on the same ledger that records
the committed request and the digest and parent of each bound receipt, and
verifies it from the ledger alone. Stdlib only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIX_FIELDS = ("failure_code", "failed_predicate", "required_evidence_or_repair",
              "retry_entrypoint", "owning_existing_goal", "next_attempt")
LEDGER_ROOTS = ("STEGVERSE_REPO_LEDGER_ROOT", "STEGVERSE_ORG_LEDGER_ROOT")


def _module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


binding = _module("committed_request_binding_under_test", "resident-runtime/committed_request_binding.py")
ingress = _module("intr_destination_ingress_for_binding", "resident-runtime/intr_destination_ingress.py")
readback = _module("organization_ledger_readback_for_binding", "resident-runtime/organization_ledger_readback.py")
REGISTRY = json.loads((ROOT / "org-boundary/registry/services.json").read_text(encoding="utf-8"))
NAMESPACE = json.loads((ROOT / ".stegverse/transition-ledger/contract.json").read_text(
    encoding="utf-8"))["repository_ledger_location"]["namespace"]
REQUEST = {"path": ".stegverse/transition-ledger/requests/a.json", "sha256": "sha256:" + "c" * 64}


class Ledgers(unittest.TestCase):
    def setUp(self):
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        self.work = Path(self._work.name)
        previous = {name: os.environ.get(name) for name in LEDGER_ROOTS}
        self.addCleanup(lambda: [os.environ.pop(n, None) if v is None else os.environ.__setitem__(n, v)
                                 for n, v in previous.items()])
        self.use_directories()

    def use_directories(self):
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = str(self.work / "repo")
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = str(self.work / "org")

    def closure(self, packet="packet-1"):
        result = ingress.self_closure("comm-1", registry=REGISTRY, packet_id=packet, hb_epoch=33)
        self.assertEqual(result["disposition"], "ALLOW", result)
        return result

    def files(self, schema):
        found = []
        for name in LEDGER_ROOTS:
            for path in Path(os.environ[name]).rglob("*.json"):
                try:
                    record = json.loads(path.read_text())
                except ValueError:
                    continue
                if record.get("schema") == schema:
                    found.append((path, record))
        return found

    def assert_refused(self, result, code):
        self.assertEqual((result["disposition"], result["failure_code"]), ("FAIL_CLOSED", code), result)
        self.assertIs(result["terminal"], True)
        for field in SIX_FIELDS:
            self.assertTrue(result.get(field), field)


class Binding(Ledgers):
    def test_the_ledger_holds_both_receipts_and_one_binding_with_matching_hashes(self):
        closure = self.closure()
        bound = binding.bind(REQUEST, closure, 33)
        self.assertEqual(bound["disposition"], "ALLOW", bound)
        (_, repository), = self.files("stegverse.repo-transition-receipt/v1")
        (_, organization), = self.files("stegverse.organization-transition-receipt/v1")
        (_, receipt), = self.files(binding.SCHEMA)
        self.assertEqual(receipt["committed_request"],
                         {**REQUEST, "hb_epoch_source": "COMMITTED_REQUEST"})
        self.assertEqual(receipt["bound_receipts"], [
            {"level": "repository", "receipt_sha256": repository["receipt_sha256"],
             "previous_receipt_sha256": repository.get("previous_receipt_sha256")},
            {"level": "organization", "receipt_sha256": organization["receipt_sha256"],
             "previous_receipt_sha256": organization.get("previous_receipt_sha256")},
        ])
        self.assertEqual(closure["repository_receipt_sha256"], repository["receipt_sha256"])
        self.assertEqual(closure["organization_receipt_sha256"], organization["receipt_sha256"])
        self.assertEqual(receipt["receipt_sha256"], binding.sha(
            {k: v for k, v in receipt.items() if k != "receipt_sha256"}))
        verified = binding.verify(REQUEST, closure)
        self.assertEqual((verified["disposition"], verified["binding_receipt_sha256"]),
                         ("ALLOW", receipt["receipt_sha256"]))

    def test_a_replay_returns_the_recorded_binding(self):
        closure = self.closure()
        first = binding.bind(REQUEST, closure, 33)
        second = binding.bind(REQUEST, closure, 33)
        self.assertIs(second["replayed"], True)
        self.assertEqual(first["binding_receipt_sha256"], second["binding_receipt_sha256"])
        self.assertEqual(len(self.files(binding.SCHEMA)), 1)

    def test_a_replay_under_a_different_request_is_refused(self):
        closure = self.closure()
        first = binding.bind(REQUEST, closure, 33)
        replayed = self.closure()  # same packet: the ingress answers with the recorded receipts
        self.assertIs(replayed["transition_replayed"], True)
        for other in ({**REQUEST, "sha256": "sha256:" + "d" * 64}, {**REQUEST, "path": "other.json"}):
            result = binding.bind(other, replayed, 33)
            self.assert_refused(result, "COMMITTED_REQUEST_REPLAY_MISMATCH")
            self.assertIn(first["binding_receipt_sha256"], result["detail"])
        self.assertEqual(len(self.files(binding.SCHEMA)), 1)

    def test_a_tampered_bound_receipt_fails_verification(self):
        closure = self.closure()
        binding.bind(REQUEST, closure, 33)
        (path, repository), = self.files("stegverse.repo-transition-receipt/v1")
        repository["evidence"]["communication_id"] = "comm-forged"
        path.write_text(json.dumps(repository))
        self.assert_refused(binding.verify(REQUEST, closure), "BOUND_RECEIPT_UNREADABLE")

    def test_a_tampered_binding_receipt_fails_verification(self):
        closure = self.closure()
        binding.bind(REQUEST, closure, 33)
        (path, receipt), = self.files(binding.SCHEMA)
        receipt["hb_epoch"] = 99
        path.write_text(json.dumps(receipt))
        self.assert_refused(binding.verify(REQUEST, closure), "BOUND_RECEIPT_UNREADABLE")

    def test_a_malformed_or_half_set_request_is_terminal_and_writes_nothing(self):
        closure = self.closure()
        for pair in ({"path": REQUEST["path"]}, {"sha256": REQUEST["sha256"]},
                     {"path": "", "sha256": REQUEST["sha256"]}, {"path": "a\nb", "sha256": REQUEST["sha256"]},
                     {"path": REQUEST["path"], "sha256": "sha256:abc"}):
            self.assert_refused(binding.bind(pair, closure, 33), "COMMITTED_REQUEST_INVALID")
        self.assertEqual(self.files(binding.SCHEMA), [])

    def test_a_missing_or_unreadable_bound_receipt_is_terminal_and_writes_nothing(self):
        closure = self.closure()
        missing = {**closure, "organization_receipt_sha256": "sha256:" + "0" * 64}
        self.assert_refused(binding.bind(REQUEST, missing, 33), "BOUND_RECEIPT_UNREADABLE")
        self.assert_refused(binding.bind(REQUEST, {**closure, "repository_receipt_sha256": None}, 33),
                            "BOUND_RECEIPT_UNREADABLE")
        (path, repository), = self.files("stegverse.repo-transition-receipt/v1")
        path.write_text("{not json")
        self.assert_refused(binding.bind(REQUEST, closure, 33), "BOUND_RECEIPT_UNREADABLE")
        self.assertEqual(self.files(binding.SCHEMA), [])

    def test_a_non_finite_value_is_never_written(self):
        with self.assertRaises(ValueError):
            binding.canon({"x": float("nan")})


class InTrIngressUnchanged(unittest.TestCase):
    #: sha256 of resident-runtime/intr_destination_ingress.py at main ecabf04 (InTr code, Grok Lane B).
    MAIN_SHA256 = "390343dc625dfe849f0dbce1bd75ae89bd4d6eccf932b985108e7ce2fe5d3eb7"

    def test_the_ingress_is_byte_identical_to_main(self):
        import hashlib
        data = (ROOT / "resident-runtime/intr_destination_ingress.py").read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), self.MAIN_SHA256)


class OnTheGitLedgerRef(Ledgers):
    def use_directories(self):
        ref = "refs/stegverse/organization-ledger"
        self.location = "git+" + str(self.work / "ledger.git") + "#" + ref
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = self.location
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = self.location + ":" + NAMESPACE

    def test_the_binding_sits_on_the_ref_and_the_readback_still_verifies(self):
        closure = self.closure()
        self.assertEqual(binding.bind(REQUEST, closure, 33)["disposition"], "ALLOW")
        self.assertEqual(binding.verify(REQUEST, closure)["disposition"], "ALLOW")
        result = readback.readback(self.location, closure, NAMESPACE)
        self.assertEqual(result["disposition"], "ALLOW", result)
        self.assertEqual(result["organization_receipt_sha256"], closure["organization_receipt_sha256"])


if __name__ == "__main__":
    unittest.main()
