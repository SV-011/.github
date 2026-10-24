"""V-P11 rows 3-5 on the library APIs: emit.append, aggregate_repo_transition.append, egress emit.

Each is a forgery probe: a refusal is six fields and terminal, raised or
returned before any ledger write (rows 3, 5) or any frame (row 4). Stdlib only.
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


emit = _module("emit_for_rows", ".stegverse/transition-ledger/emit.py")
aggregate = _module("aggregate_for_rows", "resident-runtime/aggregate_repo_transition.py")
egress = _module("egress_for_rows", "resident-runtime/organization_egress_boundary.py")
GENESIS = {"mode": "ESTABLISH_GENESIS", "node_ref": "committed-request-rows", "predecessor": None}
HALF_SET = {"path": ".stegverse/transition-ledger/requests/a.json"}  # no sha256


class Ledgers(unittest.TestCase):
    def setUp(self):
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        self.work = Path(self._work.name)
        previous = {name: os.environ.get(name) for name in LEDGER_ROOTS}
        self.addCleanup(lambda: [os.environ.pop(n, None) if v is None else os.environ.__setitem__(n, v)
                                 for n, v in previous.items()])
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = str(self.work / "repo")
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = str(self.work / "org")

    def written(self, variable):
        root = Path(os.environ[variable])
        return sorted(p.name for p in root.rglob("*.json")) if root.exists() else []

    def assert_six_field(self, refusal, code, disposition="FAIL_CLOSED"):
        self.assertEqual((refusal["disposition"], refusal["failure_code"]), (disposition, code), refusal)
        self.assertIs(refusal["terminal"], True)
        for field in SIX_FIELDS:
            self.assertTrue(refusal.get(field), field)

    def repository_receipt(self, transition_id="t-1"):
        return emit.append(transition_id, "TEST_TRANSITION", "sha256:" + "1" * 64, "sha256:" + "2" * 64,
                           {"note": "genuine"}, hb_epoch=33)


class Row3InjectedEvidence(Ledgers):
    def test_emit_append_refuses_committed_request_at_any_depth_before_any_write(self):
        for evidence in ({"committed_request": HALF_SET},
                         {"outer": {"inner": [{"committed_request": HALF_SET}]}}):
            with self.assertRaises(ValueError) as raised:
                emit.append("t-forged", "TEST_TRANSITION", "sha256:" + "1" * 64, "sha256:" + "2" * 64,
                            evidence, hb_epoch=33)
            self.assert_six_field(raised.exception.refusal, "FORGED_COMMITTED_REQUEST_FROM_EVIDENCE_JSON")
        self.assertEqual(self.written("STEGVERSE_REPO_LEDGER_ROOT"), [])

    def test_aggregate_append_refuses_committed_request_at_any_depth_before_any_write(self):
        source = self.repository_receipt()
        for evidence in ({"committed_request": HALF_SET}, {"a": [{"b": {"committed_request": {}}}]}):
            with self.assertRaises(ValueError) as raised:
                aggregate.append(source, "REPO_STATE_PROPAGATION", "p", "s", evidence, "NONE", hb_epoch=33)
            self.assert_six_field(raised.exception.refusal, "FORGED_COMMITTED_REQUEST_FROM_EVIDENCE_JSON")
        self.assertEqual(self.written("STEGVERSE_ORG_LEDGER_ROOT"), [])


class Row5SourceReceiptOnLedger(Ledgers):
    """OFF_LEDGER_SOURCE_RECEIPT_WITHOUT_BINDING_ACCEPTED: the presence check is unconditional."""

    def off_ledger_variants(self, genuine):
        body = {k: v for k, v in genuine.items() if k != "receipt_sha256"}
        plain = dict(body, evidence={"note": "fabricated"})
        carrying = dict(body, evidence={"note": "fabricated", "committed_request": HALF_SET})
        return {
            "rehashed, no committed_request": {**plain, "receipt_sha256": aggregate.sha(plain)},
            "rehashed, carrying committed_request": {**carrying, "receipt_sha256": aggregate.sha(carrying)},
            "genuine digest, tampered body": {**genuine, "evidence": {"note": "tampered"}},
            "genuine digest, tampered body carrying committed_request":
                {**genuine, "evidence": {"note": "tampered", "committed_request": HALF_SET}},
        }

    def test_a_source_receipt_off_the_repository_ledger_is_never_aggregated(self):
        genuine = self.repository_receipt()
        for name, source in self.off_ledger_variants(genuine).items():
            with self.assertRaises(ValueError, msg=name) as raised:
                aggregate.append(source, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE", hb_epoch=33)
            self.assert_six_field(raised.exception.refusal, "SOURCE_RECEIPT_NOT_ON_LEDGER")
        self.assertEqual(self.written("STEGVERSE_ORG_LEDGER_ROOT"), [])
        organization = aggregate.append(genuine, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE",
                                        hb_epoch=33)
        self.assertEqual(organization["repo_receipt_sha256"], genuine["receipt_sha256"])

    def test_a_receipt_no_longer_reachable_from_head_is_never_aggregated(self):
        first = self.repository_receipt("t-1")
        second = self.repository_receipt("t-2")
        repo = Path(os.environ["STEGVERSE_REPO_LEDGER_ROOT"])
        head = json.loads((repo / "HEAD.json").read_text())
        (repo / "HEAD.json").write_text(json.dumps({**head, "receipt_sha256": first["receipt_sha256"]}))
        with self.assertRaises(ValueError) as raised:
            aggregate.append(second, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE", hb_epoch=33)
        self.assert_six_field(raised.exception.refusal, "SOURCE_RECEIPT_NOT_ON_LEDGER")
        self.assertEqual(self.written("STEGVERSE_ORG_LEDGER_ROOT"), [])

    def test_an_absent_repository_ledger_is_never_aggregated(self):
        genuine = self.repository_receipt()
        for name in ("HEAD.json",):
            (Path(os.environ["STEGVERSE_REPO_LEDGER_ROOT"]) / name).unlink()
        with self.assertRaises(ValueError) as raised:
            aggregate.append(genuine, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE", hb_epoch=33)
        self.assert_six_field(raised.exception.refusal, "SOURCE_RECEIPT_NOT_ON_LEDGER")


class Row8DesignatedSourceLedger(Ledgers):
    """SOURCE_LEDGER_OVERRIDE_LAUNDERS_FABRICATED_RECEIPT on aggregate.append: no caller-chosen source ledger."""

    REF = "refs/stegverse/organization-ledger"
    NAMESPACE = json.loads((ROOT / ".stegverse/transition-ledger/contract.json").read_text())[
        "repository_ledger_location"]["namespace"]

    def use_git(self, repository, repo_locator=None):
        base = "git+" + str(repository) + "#" + self.REF
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = base
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = repo_locator or base + ":" + self.NAMESPACE

    def test_there_is_no_source_store_override(self):
        import inspect
        self.assertNotIn("source_store", inspect.signature(aggregate.append).parameters)

    def test_an_attacker_or_symlinked_or_foreign_source_ledger_aggregates_nothing(self):
        designated = self.work / "ledger.git"
        self.use_git(designated)
        genuine = self.repository_receipt()
        attacker = self.work / "attacker.git"
        attacker_locator = "git+" + str(attacker) + "#" + self.REF + ":" + self.NAMESPACE
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = attacker_locator
        minted = self.repository_receipt("t-minted")  # minted into the attacker's root
        (self.work / "link.git").symlink_to(attacker)
        for locator in (attacker_locator,
                        "git+" + str(self.work / "link.git") + "#" + self.REF + ":" + self.NAMESPACE,
                        "git+" + str(designated) + "#" + self.REF + ":repository-ledger/Other/repo",
                        str(self.work / "plain-dir")):
            os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = locator
            for source in (minted, genuine):
                with self.assertRaises(ValueError) as raised:
                    aggregate.append(source, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE", hb_epoch=33)
                self.assert_six_field(raised.exception.refusal, "SOURCE_LEDGER_NOT_DESIGNATED")
        self.use_git(designated)
        organization = aggregate.append(genuine, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE",
                                        hb_epoch=33)
        self.assertEqual(organization["repo_receipt_sha256"], genuine["receipt_sha256"])
        with self.assertRaises(ValueError):  # the attacker's receipt is still not on the designated ledger
            aggregate.append(minted, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE", hb_epoch=33)

    def test_a_receipt_naming_another_repository_aggregates_nothing(self):
        genuine = self.repository_receipt()
        foreign = {**genuine, "repository": "Other-Org/.github"}
        with self.assertRaises(ValueError) as raised:
            aggregate.append(foreign, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE", hb_epoch=33)
        self.assert_six_field(raised.exception.refusal, "SOURCE_LEDGER_NOT_DESIGNATED")
        self.assertEqual(self.written("STEGVERSE_ORG_LEDGER_ROOT"), [])


class Row9CorruptReceiptOnTheWalk(Ledgers):
    def test_a_corrupt_stored_receipt_is_terminal_and_a_read_fault_is_a_retry(self):
        first = self.repository_receipt("t-1")
        second = self.repository_receipt("t-2")
        repo = Path(os.environ["STEGVERSE_REPO_LEDGER_ROOT"])
        stored = repo / "receipts" / (second["receipt_sha256"][7:] + ".json")
        good = stored.read_bytes()
        for corrupt in (b"{truncated", b"[1]", b"\xff\xfe"):
            stored.write_bytes(corrupt)
            with self.assertRaises(ValueError) as raised:
                aggregate.append(first, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE", hb_epoch=33)
            self.assert_six_field(raised.exception.refusal, "SOURCE_LEDGER_RECEIPT_CORRUPT")
        stored.write_bytes(good)
        real_get = aggregate.ledger_store.Store.get

        def faulty(store, key):
            if str(store.root) == str(repo) and key.startswith("receipts/"):
                raise PermissionError("injected read fault")
            return real_get(store, key)
        aggregate.ledger_store.Store.get = faulty
        try:
            with self.assertRaises(ValueError) as raised:
                aggregate.append(first, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE", hb_epoch=33)
        finally:
            aggregate.ledger_store.Store.get = real_get
        refusal = raised.exception.refusal
        self.assertEqual((refusal["failure_code"], refusal["terminal"]), ("SOURCE_LEDGER_READ_FAULT_RETRY", False))
        for field in SIX_FIELDS:
            self.assertTrue(refusal.get(field), field)
        self.assertEqual(self.written("STEGVERSE_ORG_LEDGER_ROOT"), [])


class TrackedLedgerRoot(Ledgers):
    """LEDGER_ROOT_NOT_CONTRACT_DESIGNATED (lead 20:59Z repair (a)): a committed fake ledger is never a root."""

    def checkout_with_committed_ledger(self):
        import subprocess
        genuine = self.repository_receipt()  # a real chain, then committed into a checkout as a fake ledger
        checkout = self.work / "checkout"
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        fake = checkout / "fake-ledger"
        import shutil
        shutil.copytree(os.environ["STEGVERSE_REPO_LEDGER_ROOT"], fake)
        subprocess.run(["git", "-C", str(checkout), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(checkout), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "-m", "fake ledger"], check=True)
        return checkout, fake, genuine

    def tree(self, root):
        return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if ".git" not in p.parts)

    def test_a_committed_fake_ledger_is_refused_with_zero_writes(self):
        checkout, fake, genuine = self.checkout_with_committed_ledger()
        before = self.tree(checkout)
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = str(fake)
        with self.assertRaises(ValueError) as raised:
            aggregate.append(genuine, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE", hb_epoch=33)
        self.assert_six_field(raised.exception.refusal, "LEDGER_ROOT_NOT_CONTRACT_DESIGNATED")
        with self.assertRaises(ValueError) as raised:
            emit.append("t-2", "TEST_TRANSITION", "sha256:" + "1" * 64, "sha256:" + "2" * 64, {"n": 1}, hb_epoch=33)
        self.assert_six_field(raised.exception.refusal, "LEDGER_ROOT_NOT_CONTRACT_DESIGNATED")
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = str(checkout / "org-in-checkout")
        (checkout / "org-in-checkout").mkdir()
        (checkout / "org-in-checkout" / "HEAD.json").write_text("{}")
        import subprocess
        subprocess.run(["git", "-C", str(checkout), "add", "-A"], check=True)
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = str(self.work / "repo")
        with self.assertRaises(ValueError) as raised:
            aggregate.append(genuine, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE", hb_epoch=33)
        self.assert_six_field(raised.exception.refusal, "LEDGER_ROOT_NOT_CONTRACT_DESIGNATED")
        self.assertEqual(self.tree(checkout), before + ["org-in-checkout", "org-in-checkout/HEAD.json"])

    def test_an_untracked_directory_inside_a_checkout_stays_allowed(self):
        checkout, _, genuine = self.checkout_with_committed_ledger()
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = str(checkout / "untracked-org")
        organization = aggregate.append(genuine, "REPO_STATE_PROPAGATION", "p", "s", {"op": "x"}, "NONE",
                                        hb_epoch=33)
        self.assertEqual(organization["repo_receipt_sha256"], genuine["receipt_sha256"])


class Row4EgressPayload(Ledgers):
    def test_a_half_set_committed_request_in_an_egress_payload_publishes_zero_frames(self):
        mesh = self.work / "mesh"
        payload = {"message_class": "ecosystem.communication", "communication_id": "c-1",
                   "subject": "s", "body": {"schema": "x", "committed_request": HALF_SET}}
        result = egress.emit("StegVerse-org", payload, standing=GENESIS, capability="intr-destination",
                             mesh_root=mesh, hb_epoch=33)
        # the egress records its refusals as DENY transitions (existing convention); no frame is built
        self.assert_six_field(result, "COMMITTED_REQUEST_IN_EGRESS_PAYLOAD", disposition="DENY")
        self.assertIs(result["emitted"], False)
        self.assertEqual(result["frames_published"], 0)
        self.assertEqual(list(mesh.rglob("*.json")) if mesh.exists() else [], [])


if __name__ == "__main__":
    unittest.main()
