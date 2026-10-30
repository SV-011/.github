"""InTr mesh path ingress: the destination half (carried source receipts -> this organization's ingress receipts).

Every source receipt here is SYNTHETIC: built and sealed in this test for a path
into this organization, never read from any ledger. Destination receipts are written
to temporary copies of the pinned ledger tip. Nothing here is ledgered or
authentic. Real destination receipts appear only once an inbound row carrying
real source receipts lands on main and .github/workflows/intr-path-ingress.yml
consumes it.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER_TIP = json.loads((ROOT / "tests/fixtures/intr-path-ingress/pin.json").read_text())["ledger_tip"]
NAMESPACE = json.loads((ROOT / ".stegverse/transition-ledger/contract.json").read_text())[
    "repository_ledger_location"]["namespace"]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pi = load("intr_path_ingress_under_test", "resident-runtime/intr_path_ingress.py")
DESTINATION = pi.organization()
SOURCE = "SV-011" if DESTINATION != "SV-011" else "StegVerse-org"
OTHER = "SV-LLM" if DESTINATION != "SV-LLM" else "StegGhost"
PID = SOURCE + "->" + DESTINATION + ":intr-destination"
CID = "intr-path-v1:" + SOURCE + "->" + DESTINATION


def seal(body):
    body = {k: v for k, v in body.items() if k != "receipt_sha256"}
    return {**body, "receipt_sha256": pi.sha(body)}


def synthetic_source(path_id=PID, cid=CID, destination=DESTINATION, **evidence_overrides):
    message = {"message_class": "ecosystem.communication", "communication_id": cid,
               "body": {"schema": pi.ingress.PAYLOAD_SCHEMA, "path_id": path_id, "transfer_commit": "a" * 40}}
    core = {"schema": pi.SOURCE_EVIDENCE_SCHEMA, "repository": SOURCE + "/.github", "path_id": path_id,
            "source_organization": SOURCE, "destination_organization": destination, "capability": "intr-destination",
            "destination_address": pi.address(destination), "message": message, "message_sha256": pi.sha(message),
            "transfer_commit": "a" * 40, "merge_transfer_organization_receipt_sha256": "sha256:" + "b" * 64}
    core.update(evidence_overrides)
    evidence = {**core, "payload_sha256": pi.sha(core), "disposition": "ALLOW", "communication_id": cid}
    repo = seal({"schema": "stegverse.repo-transition-receipt/v1", "repository": SOURCE + "/.github",
                 "evidence": evidence, "synthetic": "NOT_LEDGERED_NOT_AUTHENTIC"})
    org = seal({"schema": "stegverse.organization-transition-receipt/v1", "source_repository": SOURCE + "/.github",
                "repo_receipt_sha256": repo["receipt_sha256"], "previous_receipt_sha256": None,
                "boundary_evidence": {"operation": pi.SOURCE_OPERATION, "path_id": path_id, "communication_id": cid},
                "synthetic": "NOT_LEDGERED_NOT_AUTHENTIC"})
    return org, repo


class Ledger:
    def __init__(self, inbound=None):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        subprocess.run("git -C '%s' archive %s | tar -x -C '%s'" % (ROOT, LEDGER_TIP, self.root), shell=True, check=True)
        self.saved = {k: os.environ.get(k) for k in ("STEGVERSE_ORG_LEDGER_ROOT", "STEGVERSE_REPO_LEDGER_ROOT")}
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = str(self.root)
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = str(self.root / NAMESPACE)
        self.inbound = Path(self.dir.name) / "inbound.json"

    def write_inbound(self, rows):
        self.inbound.write_text(json.dumps({"schema": pi.INBOUND_SCHEMA, "destination_repository": DESTINATION + "/.github",
                                            "transfers": rows}))
        return self.inbound

    def files(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*")
                if p.is_file() and p.name != "inbound.json"}

    def close(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.dir.cleanup()


def row(org, repo, pid=PID, cid=CID):
    return {"path_id": pid, "communication_id": cid,
            "carried": {"source_organization_receipt": org, "source_repository_receipt": repo}}


def source_readback(org, repo):
    return {"schema": pi.READBACK_SCHEMA, "repository": SOURCE + "/.github", "ledger_tip": "synthetic",
            "head_receipt_sha256": org["receipt_sha256"], "chain": [org], "organization_receipt": org,
            "repository_receipt": repo}


class Consume(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()

    def tearDown(self):
        self.ledger.close()

    def six(self, result):
        for field in pi.SIX_FIELDS:
            self.assertTrue(result.get(field), field)

    def test_no_inbound_file_is_no_transition(self):
        self.assertEqual(pi.consume(self.ledger.root / "absent.json"), [])

    def test_carried_receipts_enter_ingress_and_link_by_seal(self):
        org, repo = synthetic_source()
        before = self.ledger.files()
        (result,) = pi.consume(self.ledger.write_inbound([row(org, repo)]))
        self.assertEqual(result["disposition"], "ALLOW", result)
        self.assertEqual(result["source_inclusion"]["failure_code"], "INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED")
        self.six(result["source_inclusion"])
        after = self.ledger.files()
        for name, data in before.items():
            if not name.endswith("HEAD.json"):
                self.assertEqual(after[name], data, name)
        d_org, d_rep, _ = pi.find_destination(PID, CID)
        self.assertEqual(pi.mt._boundary(d_org)["operation"], "ORGANIZATION_INTR_DESTINATION_INGRESS")
        self.assertEqual(pi.mt._boundary(d_org)["source_organization_receipt_sha256"], org["receipt_sha256"])
        self.assertEqual(d_rep["evidence"]["source_repository_receipt_sha256"], repo["receipt_sha256"])
        (verified,) = pi.verify_inbound(self.ledger.inbound)
        self.assertEqual(verified["disposition"], "ALLOW", verified)
        (replay,) = pi.consume(self.ledger.inbound)
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.ledger.files(), after)

    def test_end_to_end_is_allow_only_with_both_ledgers_read_back(self):
        org, repo = synthetic_source()
        pi.consume(self.ledger.write_inbound([row(org, repo)]))
        destination = pi.readback(PID, CID, pi.OPERATION, "temp")
        whole = pi.verify_end_to_end(PID, CID, source_readback(org, repo), destination)
        self.assertEqual((whole["disposition"], whole["path"]), ("ALLOW", "VERIFIED"), whole)
        blind = pi.verify_end_to_end(PID, CID, None, destination)
        self.assertEqual((blind["disposition"], blind["failure_code"]),
                         ("FAIL_CLOSED", "INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED"))
        self.assertIn("StegVerse-Labs/TVC", blind["retry_entrypoint"])
        self.assertIn(SOURCE + "/.github", blind["retry_entrypoint"])
        self.six(blind)
        other_org, other_repo = synthetic_source(cid="intr-path-v1:other")
        mismatch = pi.verify_end_to_end(PID, CID, source_readback(other_org, other_repo), destination)
        self.assertEqual(mismatch["disposition"], "DENY", mismatch)
        self.six(mismatch)

    def test_destination_receipt_linking_another_source_is_denied(self):
        org, repo = synthetic_source()
        pi.consume(self.ledger.write_inbound([row(org, repo)]))
        d_org, d_rep, _ = pi.find_destination(PID, CID)
        forged = seal({**repo, "evidence": {**repo["evidence"], "tree": "x"}})
        link = {"path_id": PID, "communication_id": CID, "source_organization_receipt_sha256": org["receipt_sha256"],
                "source_repository_receipt_sha256": forged["receipt_sha256"]}
        result = pi.verify_destination_pair(d_org, d_rep, link)
        self.assertEqual((result["disposition"], result["failure_code"]),
                         ("DENY", "INTR_PATH_DESTINATION_RECEIPT_LINK_UNVERIFIED"))
        self.six(result)

    def test_refusals_write_nothing(self):
        org, repo = synthetic_source()
        before = self.ledger.files()
        cases = [
            (row(None, None), "FAIL_CLOSED", "INTR_PATH_SOURCE_RECEIPT_NOT_CARRIED"),
            (row(*synthetic_source(destination=OTHER)), "DENY", "INTR_PATH_CARRIED_RECEIPT_CONTRADICTED"),
            (row(*synthetic_source(destination_address=pi.address(OTHER))), "DENY",
             "INTR_PATH_CARRIED_RECEIPT_CONTRADICTED"),
            (row(org, synthetic_source(cid="intr-path-v1:other")[1]), "DENY", "INTR_PATH_CARRIED_RECEIPT_CONTRADICTED"),
        ]
        tampered = copy.deepcopy(org)
        tampered["boundary_evidence"]["path_id"] = "x"
        cases.append((row(tampered, repo), "FAIL_CLOSED", "INTR_PATH_SOURCE_RECEIPT_NOT_CARRIED"))
        for r, disposition, code in cases:
            with self.subTest(code=code):
                (result,) = pi.consume(self.ledger.write_inbound([r]))
                self.assertEqual((result["disposition"], result["failure_code"]), (disposition, code), result)
                self.six(result)
        self.assertEqual(self.ledger.files(), before)
        (unledgered,) = pi.verify_inbound(self.ledger.write_inbound([row(org, repo)]))
        self.assertEqual((unledgered["disposition"], unledgered["failure_code"]),
                         ("FAIL_CLOSED", "INTR_PATH_DESTINATION_RECEIPT_NOT_LEDGERED"))
        self.six(unledgered)


    def test_a_second_receipt_for_a_crossing_is_a_duplicate_and_the_oldest_is_read(self):
        org, repo = synthetic_source()
        (first,) = pi.consume(self.ledger.write_inbound([row(org, repo)]))
        self.assertEqual(first["disposition"], "ALLOW", first)
        d_org, d_rep, _ = pi.find_destination(PID, CID)
        self.assertEqual(d_org["receipt_sha256"], first["organization_receipt_sha256"])
        # A later receipt for the same crossing (a different transition id) lands on the chain.
        store = pi.mt.org_store()
        epoch, failed = pi.mt.head_epoch(store)
        self.assertIsNone(failed)
        checked = pi.check_source_pair(org, repo, PID, CID, DESTINATION)
        evidence = pi.ingress_evidence(checked, pi.source_inclusion(checked))
        later, _ = pi.ingress._append(
            pi.TRANSITION_CLASS, PID + "@" + CID + "#later", evidence, epoch, operation=pi.OPERATION,
            profile_id=pi.ingress.PROFILE_ID,
            boundary_extra={"path_id": PID, "source_repository": checked["source_repository"],
                            "source_organization_receipt_sha256": checked["source_organization_receipt_sha256"],
                            "source_repository_receipt_sha256": checked["source_repository_receipt_sha256"]})
        self.assertNotEqual(later["organization_receipt"]["receipt_sha256"], first["organization_receipt_sha256"])
        d_org, d_rep, broken = pi.find_destination(PID, CID)
        self.assertIsNone(broken)
        self.assertEqual(d_org["receipt_sha256"], first["organization_receipt_sha256"])
        (again,) = pi.consume(self.ledger.write_inbound([row(org, repo)]))
        self.assertEqual((again["disposition"], again.get("replayed")), ("ALLOW", True), again)
        (verified,) = pi.verify_inbound(self.ledger.inbound)
        self.assertEqual(verified["disposition"], "ALLOW", verified)


if __name__ == "__main__":
    unittest.main()
