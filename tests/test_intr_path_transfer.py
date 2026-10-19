"""InTr mesh path transfer: declared paths, the path-receipt append, and the two-half verifier.

Temp ledgers are the tree of the pinned ledger tip (git archive), never the
published ref; this workflow has contents: read and pushes nothing. Receipts
written here are NOT LEDGERED and NOT AUTHENTIC: they exist only in a temporary
directory for the duration of a test. Path receipts appear on the real ledger
only once the transfer declaration lands on main and
.github/workflows/intr-path-transfer.yml appends it.
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
FIXTURES = ROOT / "tests" / "fixtures" / "intr-path-transfer"
EXPECTED = json.loads((FIXTURES / "expected-real-state.json").read_text())
LEDGER_TIP = EXPECTED["ledger_tip"]
NAMESPACE = json.loads((ROOT / ".stegverse/transition-ledger/contract.json").read_text())[
    "repository_ledger_location"]["namespace"]
TRANSFER = EXPECTED["verified_transfer_commit"]   # main-merge transfer VERIFIED at the pinned tip
UNLEDGERED = EXPECTED["unledgered_commit"]        # no main-merge transfer receipt at the pinned tip


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pt = load("intr_path_transfer_under_test", "resident-runtime/intr_path_transfer.py")


class Ledger:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        subprocess.run("git -C '%s' archive %s | tar -x -C '%s'" % (ROOT, LEDGER_TIP, self.root), shell=True, check=True)
        self.saved = {k: os.environ.get(k) for k in ("STEGVERSE_ORG_LEDGER_ROOT", "STEGVERSE_REPO_LEDGER_ROOT")}
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = str(self.root)
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = str(self.root / NAMESPACE)
        for commit in EXPECTED.get("temp_merge_transfers") or []:   # temp copies only; not ledgered
            result = pt.mt.append_merge_transfer(pt.mt.commit_facts(commit))
            assert result["disposition"] == "ALLOW", result

    def files(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def close(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.dir.cleanup()


def reseal(receipt):
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    return {**body, "receipt_sha256": pt.sha(body)}


def mine():
    return [p for p in pt.declared_paths() if p["source"] == pt.organization()]


def cid(path):
    rows = {r["path_id"]: r["communication_id"] for r in pt.load_declaration()}
    return rows[path["path_id"]]


class DeclaredPaths(unittest.TestCase):
    def test_paths_are_ordered_pairs_of_conformant_organizations(self):
        paths = pt.declared_paths()
        orgs = sorted({p["source"] for p in paths})
        self.assertEqual(len(orgs), 8)
        self.assertEqual(len(paths), len(orgs) * (len(orgs) - 1))
        self.assertEqual(len({p["path_id"] for p in paths}), len(paths))
        for p in paths:
            self.assertNotEqual(p["source"], p["destination"])
            self.assertEqual(p["capability"], "intr-destination")
            self.assertTrue(p["destination_address"].endswith(".intr-destination"))

    def test_declaration_covers_every_path_from_this_organization_once(self):
        rows = pt.load_declaration()
        self.assertEqual(sorted(r["path_id"] for r in rows), sorted(p["path_id"] for p in mine()))
        self.assertEqual(len({r["communication_id"] for r in rows}), len(rows))

    def test_messages_conform_to_the_protocol(self):
        for p in mine():
            m = pt.message(p, cid(p), TRANSFER)
            self.assertEqual(pt.intr.conform_message(m, p["destination"])["disposition"], "ALLOW")


class RealState(unittest.TestCase):
    def test_every_path_is_fail_closed_at_the_pinned_tip(self):
        ledger = Ledger()
        try:
            state = pt.real_state()
        finally:
            ledger.close()
        self.assertEqual(state["counts"], {"ALLOW": 0, "DENY": 0, "FAIL_CLOSED": 56})
        self.assertEqual(state["paths"], EXPECTED["paths"])
        self.assertEqual(state["counts"], EXPECTED["counts"])


class Append(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()

    def tearDown(self):
        self.ledger.close()

    def test_append_links_the_transfer_and_the_source_half_verifies(self):
        before = self.ledger.files()
        results = pt.append_transfers(TRANSFER)
        self.assertEqual([r["disposition"] for r in results], ["ALLOW"] * 7, results)
        after = self.ledger.files()
        for name, data in before.items():
            if not name.endswith("HEAD.json"):
                self.assertEqual(after[name], data, name)
        for p in mine():
            source = pt.verify_source(p, cid(p))
            self.assertEqual(source["disposition"], "ALLOW", source)
            self.assertEqual(source["transfer_commit"], TRANSFER)
            whole = pt.verify_path(p, cid(p))
            self.assertEqual((whole["disposition"], whole["failure_code"]),
                             ("FAIL_CLOSED", "INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED"))
            self.assertEqual(whole["halves"], {"source": "ALLOW", "destination": "FAIL_CLOSED"})
            self.assertIn("StegVerse-Labs/TVC", whole["retry_entrypoint"])
            self.assertEqual(whole["classification"], "A")
            self.assertIn("TVC-PRIVATE-SOURCE-READ-001", whole["owning_existing_goal"])
            self.assertNotIn("#162", whole["owning_existing_goal"])
            for field in pt.SIX_FIELDS:
                self.assertTrue(whole[field], field)
        replay = pt.append_transfers(TRANSFER)
        self.assertTrue(all(r.get("replayed") for r in replay))
        self.assertEqual(self.ledger.files(), after)

    def test_a_ledgered_path_is_answered_from_the_ledger_on_a_later_push(self):
        pt.append_transfers(TRANSFER)
        after = self.ledger.files()
        later = pt.append_transfers(EXPECTED["later_verified_transfer_commit"])
        self.assertTrue(all(r.get("replayed") for r in later))
        self.assertEqual(self.ledger.files(), after)

    def test_a_transfer_without_a_ledgered_main_merge_writes_nothing(self):
        before = self.ledger.files()
        (result,) = pt.append_transfers(UNLEDGERED)
        self.assertEqual((result["disposition"], result["failure_code"]),
                         ("FAIL_CLOSED", "INTR_PATH_TRANSFER_COMMIT_NOT_LEDGERED"))
        for field in pt.SIX_FIELDS:
            self.assertTrue(result[field], field)
        self.assertEqual(self.ledger.files(), before)

    def stores_with(self, edit):
        """Append, then rewrite the first path's repository receipt evidence, reseal it and relink the org receipt."""
        pt.append_transfers(TRANSFER)
        org, rep = pt.mt.org_store(), pt.mt.repo_store()
        key = pt.mt.store_module().receipt_key
        p = mine()[0]
        found = pt.verify_source(p, cid(p))
        org_r = org.get(key(found["organization_receipt_sha256"]))
        rep_r = rep.get(key(found["repository_receipt_sha256"]))
        evidence = copy.deepcopy(rep_r["evidence"])
        edit(evidence)
        evidence["payload_sha256"] = pt.sha({k: v for k, v in evidence.items()
                                             if k not in ("payload_sha256", "disposition", "communication_id")})
        new_rep = reseal({**rep_r, "evidence": evidence})
        new_org = reseal({**org_r, "repo_receipt_sha256": new_rep["receipt_sha256"]})

        class Store(dict):
            def get(self, k):
                if k not in self:
                    raise KeyError(k)
                return dict.__getitem__(self, k)
        chain = pt._chain(org)
        o = Store()
        receipts = {d: r for d, r in chain}
        # the edited receipt sits in its own position on the chain: rebuild links above it
        order = [d for d, _ in chain]
        i = order.index(found["organization_receipt_sha256"])
        receipts[order[i]] = new_org
        prev = new_org["receipt_sha256"]
        for d in reversed(order[:i]):
            r = reseal({**receipts[d], "previous_receipt_sha256": prev})
            receipts[d] = r
            prev = r["receipt_sha256"]
        for r in receipts.values():
            o[key(r["receipt_sha256"])] = r
        o[pt.mt.store_module().HEAD_KEY] = {"receipt_sha256": prev}
        rs = Store({key(new_rep["receipt_sha256"]): new_rep})
        return p, o, rs

    def test_contradicted_path_receipts_are_denied(self):
        cases = {
            "path": lambda e: e.update(destination_organization="AaCT-E"),
            "address": lambda e: e.update(destination_address="aact-e.intr-destination"),
            "transfer": lambda e: e.update(transfer_commit=UNLEDGERED),
            "message": lambda e: e["message"].update(communication_id="other"),
        }
        for name, edit in cases.items():
            with self.subTest(name=name):
                ledger = Ledger()
                try:
                    p, o, rs = self.stores_with(edit)
                    result = pt.verify_source(p, cid(p), o, rs)
                finally:
                    ledger.close()
                self.assertEqual((result["disposition"], result["failure_code"]),
                                 ("DENY", "INTR_PATH_RECEIPT_LINK_UNVERIFIED"), result)
                for field in pt.SIX_FIELDS:
                    self.assertTrue(result[field], field)


class DestinationHalf(unittest.TestCase):
    """Destination receipts here are SYNTHETIC (built and sealed in this test), not read from any ledger."""

    def readback(self, p, source, communication_id, link=None):
        link = link or source
        evidence_core = {"schema": "stegverse.intr-path-ingress-evidence/v1", "path_id": p["path_id"],
                         "source_organization_receipt_sha256": link["organization_receipt_sha256"],
                         "source_repository_receipt_sha256": link["repository_receipt_sha256"]}
        evidence = {**evidence_core, "payload_sha256": pt.sha(evidence_core), "disposition": "ALLOW",
                    "communication_id": communication_id}
        repo = reseal({"schema": "stegverse.repo-transition-receipt/v1", "repository": p["destination"] + "/.github",
                       "evidence": evidence, "synthetic": "NOT_LEDGERED_NOT_AUTHENTIC"})
        org = reseal({"schema": "stegverse.organization-transition-receipt/v1",
                      "source_repository": p["destination"] + "/.github", "repo_receipt_sha256": repo["receipt_sha256"],
                      "previous_receipt_sha256": None, "synthetic": "NOT_LEDGERED_NOT_AUTHENTIC",
                      "boundary_evidence": {"operation": "ORGANIZATION_INTR_DESTINATION_INGRESS", "path_id": p["path_id"],
                                            "communication_id": communication_id,
                                            "source_organization_receipt_sha256": link["organization_receipt_sha256"],
                                            "source_repository_receipt_sha256": link["repository_receipt_sha256"]}})
        return {"schema": "stegverse.intr-path-ledger-readback/v1", "repository": p["destination"] + "/.github",
                "head_receipt_sha256": org["receipt_sha256"], "chain": [org], "organization_receipt": org,
                "repository_receipt": repo}

    def test_both_halves_verify_only_with_a_destination_readback(self):
        ledger = Ledger()
        try:
            pt.append_transfers(TRANSFER)
            p = mine()[0]
            source = pt.verify_source(p, cid(p))
            whole = pt.verify_path(p, cid(p), destination_readback=self.readback(p, source, cid(p)))
            other = dict(source, repository_receipt_sha256="sha256:" + "0" * 64)
            denied = pt.verify_path(p, cid(p), destination_readback=self.readback(p, source, cid(p), link=other))
        finally:
            ledger.close()
        self.assertEqual((whole["disposition"], whole["path"]), ("ALLOW", "VERIFIED"), whole)
        self.assertEqual((denied["disposition"], denied["failure_code"]),
                         ("DENY", "INTR_PATH_DESTINATION_RECEIPT_LINK_UNVERIFIED"))

    def test_destination_refusals(self):
        p = mine()[0]
        absent = pt.verify_destination(p, cid(p))
        self.assertEqual((absent["disposition"], absent["failure_code"], absent["classification"]),
                         ("FAIL_CLOSED", "INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED", "A"))
        for field in pt.SIX_FIELDS:
            self.assertTrue(absent[field], field)


if __name__ == "__main__":
    unittest.main()
