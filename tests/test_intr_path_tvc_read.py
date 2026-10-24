"""InTr path: the other organization's ledger through the TV/TVC issuance transition.

TVC read receipts here are FIXTURES (evidence_class FIXTURE_NOT_AUTHENTIC): the
exported one is a byte copy of StegVerse-Labs/TVC#528
tests/fixtures/intr-path-ledger-read/, and the others are built by ``tvc_receipt``,
which mirrors TVC's collect field for field (checked against the exported copy).
No token, no network, no clock. Ledger writes go to temporary copies of the
pinned tip. Nothing here is ledgered or authentic.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import pytest
    pytestmark = pytest.mark.repository_ledger_locus
except (ImportError, AttributeError):
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import test_intr_path_ingress as ti  # noqa: E402  (same module objects: pi, synthetic_source, Ledger)

pi = ti.pi
tr = pi.tvc_read
EXPORTED = ROOT / "tests/fixtures/intr-path-tvc-read/receipt-SV-011--StegVerse-org-source_inclusion.json"
EXPORTED_SHA256 = "41c6d58d3958a38ba4c3ce6bb6d1ce656dda4a6d2b2ee0cf44d7d1ab3a5de684"
TIP = "1" * 40
EPOCH = 4096


def tvc_receipt(readback, path_id, cid, purpose, reader, read_repository, *, tip=TIP, epoch=EPOCH,
                evidence_class=tr.FIXTURE, decision=None, execution_receipt_sha256=None):
    """Field for field what StegVerse-Labs/TVC scripts/run_intr_path_ledger_reads.py collect writes."""
    source, destination = path_id.split(":")[0].split("->")
    mid = ("intr-path-" + source.replace("_", "-") + "--" + destination + "-" + purpose.replace("_", "-") + "-" + tip[:12])
    req = {"caller_repository": "StegVerse-Labs/TVC", "source_repository": read_repository,
           "consumer_task": tr.CONSUMER_TASK, "reference_mode": "TRACKED_REF", "exact_ref": tr.LEDGER_REF,
           "exact_sha": tip, "materialization_id": mid[:128], "ttl_seconds": 600}
    if readback is not None:
        readback = {**readback, "ledger_tip": tip, "ledger_head_epoch": epoch}
    body = {
        "schema": tr.RECEIPT_SCHEMA, "capability_id": tr.CAPABILITY_ID, "policy_commit_sha": tr.POLICY_COMMIT,
        "consumer_task": tr.CONSUMER_TASK,
        "read_route": ("TVC read grant: tvc.private-source-read.v1 under TV policy c6eede49 (TRACKED_REF "
                       "refs/stegverse/organization-ledger), resident executor execute_private_source_read_resident.run"),
        "evidence_class": evidence_class, "scope_id": path_id + "#" + purpose, "path_id": path_id,
        "communication_id": cid, "purpose": purpose, "operation_read": tr.PURPOSES[purpose],
        "reader_organization": reader, "read_repository": read_repository, "exact_ref": tr.LEDGER_REF,
        "contents_scope": "contents:read", "request_sha256": tr.sha256(req), "materialization_id": req["materialization_id"],
        "requested_ledger_tip": tip, "authorized_exact_sha": tip, "observed_exact_sha": tip, "execution_state": "COMPLETE",
        "execution_receipt_sha256": execution_receipt_sha256,
        "ledger_head_receipt_sha256": (readback or {}).get("head_receipt_sha256"),
        "ledger_head_epoch": (readback or {}).get("ledger_head_epoch"),
        "readback": readback, "readback_sha256": tr.sha256(readback) if readback is not None else None,
        **(decision or {"disposition": "ALLOW"}),
        "lane_credential_used": False, "credential_value_exposed": False, "write_authority": False,
        "release_authority": False, "publication_authority": False, "runtime_authority": False,
        "authority_effect": "NONE_READ_ONLY_LEDGER_READBACK", "coordination_ref": "StegVerse-org/.github#162",
    }
    body["receipt_sha256"] = tr.sha256(body)
    return body


def six(case, result):
    for field in tr.SIX_FIELDS:
        case.assertTrue(result.get(field), field)


class Contract(unittest.TestCase):
    def test_exported_tvc_fixture_is_pinned_and_mirrored(self):
        raw = EXPORTED.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), EXPORTED_SHA256)
        exported = json.loads(raw)
        rb = {k: v for k, v in exported["readback"].items() if k not in ("ledger_tip", "ledger_head_epoch")}
        mirrored = tvc_receipt(rb, exported["path_id"], exported["communication_id"], exported["purpose"],
                               exported["reader_organization"], exported["read_repository"],
                               execution_receipt_sha256=exported["execution_receipt_sha256"])
        self.assertEqual(mirrored, exported)

    def test_exported_fixture_verifies_only_as_a_fixture(self):
        exported = json.loads(EXPORTED.read_text())
        args = (exported["path_id"], "source_inclusion", "StegVerse-org", "SV-011/.github")
        rb, refused = tr.verify_receipt(exported, *args, allow_fixture=True)
        self.assertIsNone(refused, refused)
        self.assertTrue(pi._included(rb, rb["organization_receipt"]))
        none, refused = tr.verify_receipt(exported, *args)
        self.assertIsNone(none)
        self.assertEqual((refused["disposition"], refused["failure_code"]),
                         ("FAIL_CLOSED", "INTR_PATH_TVC_READ_RECEIPT_NOT_RESIDENT"))
        six(self, refused)

    def test_absent_receipt_is_fail_closed_with_retry_at_the_tvc_transition(self):
        with tempfile.TemporaryDirectory() as td:
            rb, refused = tr.lookup(ti.PID, "source_inclusion", ti.DESTINATION, ti.SOURCE + "/.github", directory=td)
        self.assertIsNone(rb)
        self.assertEqual((refused["disposition"], refused["failure_code"]),
                         ("FAIL_CLOSED", "INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED"))
        six(self, refused)
        self.assertIn("StegVerse-Labs/TVC", refused["retry_entrypoint"])
        self.assertIn("run_intr_path_ledger_reads.py request", refused["retry_entrypoint"])
        self.assertIn("TVC-PRIVATE-SOURCE-READ-001", refused["owning_existing_goal"])
        self.assertNotIn("#162", refused["owning_existing_goal"])
        for banned in ("FEDERATION_HOP_TOKEN", "FEDERATION_READ_TOKEN"):
            self.assertNotIn(banned, json.dumps(refused).replace("FEDERATION_* token", ""))

    def test_contradicted_or_refused_receipts(self):
        exported = json.loads(EXPORTED.read_text())
        args = (exported["path_id"], "source_inclusion", "StegVerse-org", "SV-011/.github")
        tampered = copy.deepcopy(exported)
        tampered["readback"]["ledger_tip"] = "2" * 40
        wrong_reader = tr.verify_receipt(exported, exported["path_id"], "source_inclusion", "SV-LLM", "SV-011/.github",
                                         allow_fixture=True)[1]
        resealed = copy.deepcopy(tampered)
        resealed["receipt_sha256"] = tr.sha256({k: v for k, v in resealed.items() if k != "receipt_sha256"})
        tvc_refused = tvc_receipt(None, exported["path_id"], exported["communication_id"], "source_inclusion",
                                  "StegVerse-org", "SV-011/.github",
                                  decision={"disposition": "FAIL_CLOSED", "failure_code": "INTR_PATH_LEDGER_READ_NOT_COMPLETE",
                                            "failed_predicate": "EXECUTION_RECEIPT_STATE_IS_COMPLETE",
                                            "required_evidence_or_repair": "x", "retry_entrypoint": "y",
                                            "owning_existing_goal": "TVC-PRIVATE-SOURCE-READ-001", "next_attempt": "z"})
        cases = [
            (tr.verify_receipt(tampered, *args, allow_fixture=True)[1], "DENY", "INTR_PATH_TVC_READ_RECEIPT_CONTRADICTED"),
            (wrong_reader, "DENY", "INTR_PATH_TVC_READ_RECEIPT_CONTRADICTED"),
            (tr.verify_receipt(resealed, *args, allow_fixture=True)[1], "DENY", "INTR_PATH_TVC_READ_RECEIPT_CONTRADICTED"),
            (tr.verify_receipt(tvc_refused, *args, allow_fixture=True)[1], "FAIL_CLOSED",
             "INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED"),
        ]
        for result, disposition, code in cases:
            with self.subTest(code=code, disposition=disposition):
                self.assertEqual((result["disposition"], result["failure_code"]), (disposition, code), result)
                six(self, result)
        self.assertEqual(cases[-1][0]["tvc_refusal"]["failure_code"], "INTR_PATH_LEDGER_READ_NOT_COMPLETE")


class EndToEnd(unittest.TestCase):
    """Both halves from fixtures: the source half read through a TVC read receipt, the destination half from this ledger."""

    def setUp(self):
        self.ledger = ti.Ledger()
        self.receipts = tempfile.TemporaryDirectory()
        self.saved = tr.RECEIPT_DIR, pi.TVC_FIXTURE_OK
        tr.RECEIPT_DIR, pi.TVC_FIXTURE_OK = Path(self.receipts.name), True

    def tearDown(self):
        tr.RECEIPT_DIR, pi.TVC_FIXTURE_OK = self.saved
        self.receipts.cleanup()
        self.ledger.close()

    def land(self, org, repo, **kw):
        rb = ti.source_readback(org, repo)
        receipt = tvc_receipt(rb, ti.PID, ti.CID, "source_inclusion", ti.DESTINATION, ti.SOURCE + "/.github", **kw)
        tr.receipt_file(ti.PID, "source_inclusion", tr.RECEIPT_DIR).write_text(json.dumps(receipt, indent=2))
        return receipt

    def test_without_a_receipt_the_path_is_fail_closed_at_the_tvc_transition(self):
        org, repo = ti.synthetic_source()
        (result,) = pi.consume(self.ledger.write_inbound([ti.row(org, repo)]))
        self.assertEqual(result["disposition"], "ALLOW", result)
        inc = result["source_inclusion"]
        self.assertEqual((inc["disposition"], inc["failure_code"]),
                         ("FAIL_CLOSED", "INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED"))
        self.assertIn("TVC-PRIVATE-SOURCE-READ-001", inc["owning_existing_goal"])
        whole = pi.end_to_end_via_tvc(ti.PID, ti.CID)
        self.assertEqual((whole["disposition"], whole["failure_code"], whole["path"]),
                         ("FAIL_CLOSED", "INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED", "UNVERIFIED"))
        six(self, whole)

    def test_with_a_verified_tvc_receipt_both_halves_verify(self):
        org, repo = ti.synthetic_source()
        self.land(org, repo)
        (result,) = pi.consume(self.ledger.write_inbound([ti.row(org, repo)]))
        self.assertEqual(result["disposition"], "ALLOW", result)
        self.assertEqual((result["source_inclusion"]["disposition"], result["source_inclusion"]["state"]),
                         ("ALLOW", "VERIFIED_BY_TVC_READ_RECEIPT"))
        d_org, d_rep, _ = pi.find_destination(ti.PID, ti.CID)
        self.assertEqual(d_rep["evidence"]["source_inclusion"]["state"], "VERIFIED_BY_TVC_READ_RECEIPT")
        self.assertEqual(d_rep["evidence"]["source_inclusion"]["source_ledger_tip"], TIP)
        whole = pi.end_to_end_via_tvc(ti.PID, ti.CID, ledger_tip="temp")
        self.assertEqual((whole["disposition"], whole["path"]), ("ALLOW", "VERIFIED"), whole)
        self.assertEqual(whole["source_ledger_tip"], TIP)

    def test_carried_copies_not_on_the_source_chain_are_denied(self):
        org, repo = ti.synthetic_source()
        other_org, other_repo = ti.synthetic_source(cid="intr-path-v1:other")
        self.land(other_org, other_repo)
        (result,) = pi.consume(self.ledger.write_inbound([ti.row(org, repo)]))
        inc = result["source_inclusion"]
        self.assertEqual((inc["disposition"], inc["failure_code"]), ("DENY", "INTR_PATH_RECEIPT_LINK_UNVERIFIED"), inc)
        six(self, inc)

    def test_resident_class_is_required_outside_tests(self):
        org, repo = ti.synthetic_source()
        self.land(org, repo)
        pi.TVC_FIXTURE_OK = False
        (result,) = pi.consume(self.ledger.write_inbound([ti.row(org, repo)]))
        self.assertEqual(result["source_inclusion"]["failure_code"], "INTR_PATH_TVC_READ_RECEIPT_NOT_RESIDENT")
        d_org, d_rep, _ = pi.find_destination(ti.PID, ti.CID)
        self.assertEqual(d_rep["evidence"]["source_inclusion"], "UNOBSERVED_WITHOUT_A_CROSS_ORG_READ_CREDENTIAL")


class RealState(unittest.TestCase):
    def test_no_tvc_read_receipt_is_landed_on_this_branch(self):
        """Real state: no resident TVC read receipt exists yet, so every cross-organization half is FAIL_CLOSED at TVC."""
        landed = sorted(p.name for p in tr.RECEIPT_DIR.glob("*.json")) if tr.RECEIPT_DIR.is_dir() else []
        self.assertEqual(landed, [])


if __name__ == "__main__":
    unittest.main()
