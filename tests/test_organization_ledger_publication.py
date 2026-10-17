"""The organization ledger ref can take an intr-destination self-closure.

The git repository in these tests is temporary. The published ref
``refs/stegverse/organization-ledger`` on this checkout is not opened for
writing. Receipts here are transition evidence on a supplied store.
``production_receipt_claimed`` stays false.

Authority and time are constraint variables. A disposition is transition
evidence.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROFILE_FILES = (
    ".stegverse/transition-ledger/contract.json",
    ".stegverse/transition-ledger/org-contract.json",
    ".stegverse/transition-ledger/emit.py",
    "resident-runtime/ledger_store.py",
    "resident-runtime/aggregate_repo_transition.py",
    "resident-runtime/organization_ledger_readback.py",
    "resident-runtime/intr_destination_ingress.py",
    ".github/workflows/organization-ledger-transition.yml",
    "tests/test_organization_ledger_publication.py",
)

BYTE_COPIES = {
    "org-boundary/runtime/intr_transport.py":
        "0f48bb46617aeba98b750eecd406d9ab157eb9e473aae583105dafd52ed7205c",
    "org-boundary/runtime/process_boundary.py":
        "a5af99cbf166422e1c40c72eaafb64342c731398a6d434a055ea485aa3782d0e",
    "resident-runtime/org_boundary_runtime.py":
        "1fedfdf46e854935e60260dab839ebd17d23df538ddfd1d0948828e1a978fa0b",
}


def _module(name, relative):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger_store = _module("ledger_store_publication", "resident-runtime/ledger_store.py")
readback = _module("organization_ledger_readback_publication",
                   "resident-runtime/organization_ledger_readback.py")
ingress = _module("intr_destination_ingress_publication",
                  "resident-runtime/intr_destination_ingress.py")

REGISTRY = json.loads((ROOT / "org-boundary/registry/services.json").read_text(encoding="utf-8"))
FEDERATION = json.loads((ROOT / "org-boundary/registry/federation.json").read_text(encoding="utf-8"))


class ContractTests(unittest.TestCase):
    def test_boundary_byte_copies_and_the_federation_directory_stay_unchanged(self):
        import hashlib
        for relative, digest in BYTE_COPIES.items():
            observed = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(observed, digest, relative)
        self.assertIs(FEDERATION["capability_services_enumerated"], False)
        for entry in FEDERATION["organizations"]:
            for key in ("org_control_service", "addressed_service"):
                self.assertNotIn("intr-destination", str(entry.get(key) or ""))

    def test_contracts_name_this_organizations_ref_and_use_constraint_variables(self):
        located = readback.designated_locators(ROOT)
        self.assertEqual(located["ref"], "refs/stegverse/organization-ledger")
        self.assertEqual(located["namespace"], "repository-ledger/SV-011/.github")
        self.assertTrue(located["organization_locator"].startswith("git+"))
        self.assertIn("#refs/stegverse/organization-ledger", located["organization_locator"])
        self.assertTrue(located["repository_locator"].endswith(":" + located["namespace"]))
        self.assertIs(located["production_receipt_claimed"], False)
        self.assertEqual(located["authority_constraint"], "NONE_LOCATOR_ONLY")
        self.assertEqual(located["time_constraint"], "HEARTBEAT_EPOCH")
        needle_verb = "g" + "rant"
        needle_field = "authority_" + "effect"
        for relative in PROFILE_FILES:
            text = (ROOT / relative).read_text(encoding="utf-8").lower()
            self.assertNotIn(needle_verb, text, relative)
            self.assertNotIn(needle_field, text, relative)

    def test_a_ref_that_is_not_fully_qualified_is_refused(self):
        with self.assertRaises(ledger_store.LedgerLocatorInvalid):
            ledger_store.parse_locator("git+/tmp/ledger#main")


class TemporaryRefClosureTests(unittest.TestCase):
    def setUp(self):
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        self.repo = Path(self._work.name) / "ledger"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        contract = json.loads(
            (ROOT / ".stegverse/transition-ledger/contract.json").read_text(encoding="utf-8"))
        self.ref = contract["repository_ledger_location"]["ref"]
        self.namespace = contract["repository_ledger_location"]["namespace"]
        resolved = self.repo.resolve()
        self.org_locator = "git+" + str(resolved) + "#" + self.ref
        self.repo_locator = self.org_locator + ":" + self.namespace
        previous = {
            "STEGVERSE_ORG_LEDGER_ROOT": os.environ.get("STEGVERSE_ORG_LEDGER_ROOT"),
            "STEGVERSE_REPO_LEDGER_ROOT": os.environ.get("STEGVERSE_REPO_LEDGER_ROOT"),
            "STEGVERSE_COMMUNICATION_ID": os.environ.get("STEGVERSE_COMMUNICATION_ID"),
            "STEGVERSE_PACKET_ID": os.environ.get("STEGVERSE_PACKET_ID"),
            "STEGVERSE_HB_EPOCH": os.environ.get("STEGVERSE_HB_EPOCH"),
        }

        def restore():
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.addCleanup(restore)
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = self.org_locator
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = self.repo_locator
        self.checkout_ref_before = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", self.ref],
            cwd=ROOT, capture_output=True)

    def test_self_closure_appends_repo_and_org_receipts_and_a_fresh_fetch_reads_them(self):
        communication_id = "closure-communication-001"
        packet_id = "closure-packet-001"
        first = ingress.self_closure(communication_id, registry=REGISTRY, packet_id=packet_id,
                                     hb_epoch=32)
        self.assertEqual(first["disposition"], "ALLOW", first.get("detail"))
        self.assertEqual(first["communication_id"], communication_id)
        self.assertIs(first["repository_receipt_observed"], True)
        self.assertIs(first["organization_receipt_observed"], True)
        self.assertIs(first["organization_receipt_preserves_repository_receipt"], True)
        self.assertIs(first["transition_replayed"], False)
        self.assertIs(first["production_receipt_claimed"], False)
        self.assertEqual(first["authority_constraint"], "NONE_DIAGNOSTIC_ACK_ONLY")
        self.assertEqual(first["time_constraint"], "HEARTBEAT_EPOCH")
        self.assertIs(first["disposition_is_transition_evidence"], True)

        tip = subprocess.check_output(["git", "rev-parse", self.ref], cwd=self.repo).strip()
        second = ingress.self_closure(communication_id, registry=REGISTRY, packet_id=packet_id,
                                      hb_epoch=40)
        self.assertIs(second["transition_replayed"], True)
        self.assertEqual(second["repository_receipt_sha256"], first["repository_receipt_sha256"])
        self.assertEqual(second["organization_receipt_sha256"], first["organization_receipt_sha256"])
        self.assertEqual(subprocess.check_output(["git", "rev-parse", self.ref], cwd=self.repo).strip(), tip)

        out = Path(self._work.name) / "ingress.json"
        completed = subprocess.run(
            [os.environ.get("PYTHON", "python3"),
             str(ROOT / "resident-runtime/intr_destination_ingress.py"),
             "--self-closure",
             "--registry", str(ROOT / "org-boundary/registry/services.json"),
             "--out", str(out)],
            check=False, capture_output=True, text=True,
            env={**os.environ,
                 "STEGVERSE_COMMUNICATION_ID": communication_id,
                 "STEGVERSE_PACKET_ID": packet_id,
                 "STEGVERSE_HB_EPOCH": "32",
                 "STEGVERSE_ORG_LEDGER_ROOT": self.org_locator,
                 "STEGVERSE_REPO_LEDGER_ROOT": self.repo_locator})
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        cli = json.loads(out.read_text(encoding="utf-8"))
        self.assertIs(cli["transition_replayed"], True)
        self.assertEqual(cli["communication_id"], communication_id)
        self.assertIs(cli["production_receipt_claimed"], False)

        fresh = Path(self._work.name) / "fresh.git"
        subprocess.run(["git", "init", "--bare", "-q", str(fresh)], check=True)
        fetched = subprocess.run(
            ["git", "--git-dir", str(fresh), "fetch", "-q", str(self.repo),
             "+" + self.ref + ":" + self.ref],
            check=False, capture_output=True, text=True)
        self.assertEqual(fetched.returncode, 0, fetched.stderr)
        # A ledger from genesis holds no declared legacy receipt, so it is read
        # with a contract that declares no segment (C6 convergence b, DEFAULT).
        segmentless = json.loads((ROOT / ".stegverse/transition-ledger/org-contract.json").read_text(encoding="utf-8"))
        segmentless["source_receipt_retention"].pop("legacy_pre_retention_segment")
        verified = readback.readback(
            "git+" + str(fresh.resolve()) + "#" + self.ref, cli, self.namespace, None, segmentless)
        self.assertNotIn("disposition", verified)
        self.assertEqual(verified["readback"], "OBSERVATION_ONLY")
        self.assertIs(verified["records_no_transition"], True)
        self.assertIs(verified["verification"]["organization_chain_verifies"], True)
        self.assertIs(verified["verification"]["repository_chain_verifies"], True)
        self.assertIs(verified["closure_result_matches_receipt_evidence"], True)
        self.assertEqual(verified["communication_id"], communication_id)
        self.assertIs(verified["communication_id_on_repository_receipt"], True)
        self.assertIs(verified["communication_id_on_organization_receipt"], True)
        self.assertIs(verified["organization_receipt_preserves_repository_receipt"], True)
        self.assertIs(verified["production_receipt_claimed"], False)
        self.assertEqual(verified["chain_length"], 1)
        self.assertEqual(verified["repository_ledger"]["chain_length"], 1)
        self.assertEqual(verified["repository_receipt_sha256"], first["repository_receipt_sha256"])
        self.assertEqual(verified["organization_receipt_sha256"], first["organization_receipt_sha256"])
        self.assertNotEqual(self.repo.resolve(), ROOT.resolve())
        checkout_after = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", self.ref],
            cwd=ROOT, capture_output=True)
        self.assertEqual(checkout_after.returncode, self.checkout_ref_before.returncode)
        self.assertEqual(checkout_after.stdout, self.checkout_ref_before.stdout)


if __name__ == "__main__":
    unittest.main()
