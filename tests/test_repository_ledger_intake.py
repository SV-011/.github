"""H29(a): SV-011/entity repository-ledger intake binding, read-only and fail-closed."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("repository_ledger_intake", ROOT / "resident-runtime/repository_ledger_intake.py")
rli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rli)

MEMBER = "SV-011/entity"
REF = "refs/stegverse/organization-ledger"
NS = "repository-ledger/" + MEMBER
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}


def org() -> dict:
    return json.loads((ROOT / ".stegverse/transition-ledger/org-contract.json").read_text(encoding="utf-8"))


def receipt(previous=None, n=0, repository=MEMBER) -> dict:
    body = {"schema": "stegverse.repo-transition-receipt/v1", "repository": repository, "sequence": n,
            "transition": "REPOSITORY_LEDGER_INITIALIZATION" if previous is None else "T",
            "previous_receipt_sha256": previous}
    return {**body, "receipt_sha256": rli.seal(body)}


def assert_six(case, result, code):
    case.assertEqual(result["failure_code"], code)
    case.assertIn(result["disposition"], ("DENY", "FAIL_CLOSED"))
    for key in rli.SIX:
        case.assertTrue(str(result[key]).strip(), key)


def ledger(files: dict) -> Path:
    repo = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=ENV)
    for key, doc in files.items():
        path = repo / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=ENV)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "ledger"], check=True, env=ENV)
    subprocess.run(["git", "-C", str(repo), "update-ref", REF, "HEAD"], check=True, env=ENV)
    return repo


def chain_files(receipts, namespace=NS) -> dict:
    files = {namespace + "/receipts/" + r["receipt_sha256"].split(":", 1)[1] + ".json": r for r in receipts}
    files[namespace + "/HEAD.json"] = {"receipt_sha256": receipts[-1]["receipt_sha256"]}
    return files


class Binding(unittest.TestCase):
    def test_entity_binding_declared_in_431_shape(self):
        binding, failure = rli.binding_for(MEMBER, org())
        self.assertIsNone(failure)
        location = binding["repository_ledger_location"]
        self.assertEqual((location["repository"], location["ref"], location["namespace"], location["new_ledger_root"]),
                         ("SV-011/.github", REF, NS, False))
        self.assertIsNone(location["expected_genesis_receipt_sha256"])
        store = binding["ledger_designation"]["stores"]
        self.assertEqual(store, [{"store_type": "GIT_REF", "provider": "git", "repository": "SV-011/.github",
                                  "ref": REF, "namespace": NS}])
        self.assertIn("a2237bc38316f3abe362bcf7f06a2356ac3a0abd", org()["member_repository_ledgers"]["source_shape"])

    def test_undeclared_member_fails_closed(self):
        _, failure = rli.binding_for("SV-011/other", org())
        assert_six(self, failure, "REPOSITORY_LEDGER_BINDING_NOT_DECLARED")

    def mutate(self, change):
        contract = org()
        change(contract["member_repository_ledgers"]["bindings"][0])
        return rli.binding_for(MEMBER, contract)[1]

    def test_kv_on_git_ref_fails_closed(self):
        failure = self.mutate(lambda b: b["ledger_designation"].update(endpoint_kind="KV"))
        assert_six(self, failure, "KV_LEDGER_DESIGNATED_ON_GIT_REF")

    def test_two_stores_fail_closed(self):
        failure = self.mutate(lambda b: b["ledger_designation"]["stores"].append(dict(b["ledger_designation"]["stores"][0])))
        assert_six(self, failure, "ENDPOINT_LEDGER_MULTI_PROVIDER_NOT_SUPPORTED")

    def test_namespace_and_new_root_are_checked(self):
        for change in (lambda b: b["repository_ledger_location"].update(namespace="repository-ledger/SV-011/.github"),
                       lambda b: b["repository_ledger_location"].update(new_ledger_root=True),
                       lambda b: b["ledger_designation"]["stores"][0].update(ref="refs/heads/main"),
                       lambda b: b["ledger_designation"].update(head_signer_source=None)):
            assert_six(self, self.mutate(change), "REPOSITORY_LEDGER_BINDING_INVALID")


class Intake(unittest.TestCase):
    def test_absent_entity_receipt_fails_closed(self):
        result = rli.intake(MEMBER, None, org())
        assert_six(self, result, "SV011_ENTITY_REPOSITORY_RECEIPT_ABSENT")
        self.assertEqual(result["failed_predicate"], "SV011_ENTITY_BOUND_AS_REPOSITORY_LEDGER")
        assert_six(self, result["append_leg"], "REPOSITORY_LEDGER_INTAKE_NOT_PRESENT")

    def test_entity_local_v0_1_receipt_is_not_consumed(self):
        result = rli.intake(MEMBER, {"schema": "stegverse.transition-ledger.receipt/v0.1", "entity_id": "SV-011"}, org())
        assert_six(self, result, "REPOSITORY_RECEIPT_SCHEMA_NOT_CONSUMED")

    def test_other_repository_is_denied(self):
        result = rli.intake(MEMBER, receipt(repository="SV-011/.github"), org())
        assert_six(self, result, "REPOSITORY_RECEIPT_NOT_FOR_BOUND_REPOSITORY")
        self.assertEqual(result["disposition"], "DENY")

    def test_unsealed_receipt_fails_closed(self):
        tampered = {**receipt(), "sequence": 9}
        assert_six(self, rli.intake(MEMBER, tampered, org()), "REPOSITORY_RECEIPT_UNSEALED")

    def test_sealed_receipt_stops_at_unpinned_genesis(self):
        assert_six(self, rli.intake(MEMBER, receipt(), org()), "ORG_LEDGER_GENESIS_NOT_PINNED")

    def test_pinned_genesis_still_has_no_append_path(self):
        contract = org()
        genesis = receipt()["receipt_sha256"]
        binding = contract["member_repository_ledgers"]["bindings"][0]
        binding["repository_ledger_location"]["expected_genesis_receipt_sha256"] = genesis
        binding["ledger_designation"]["expected_genesis_receipt_sha256"] = genesis
        result = rli.intake(MEMBER, receipt(), contract)
        assert_six(self, result, "REPOSITORY_LEDGER_INTAKE_NOT_PRESENT")
        self.assertIn("R-2", result["required_evidence_or_repair"])


class Readback(unittest.TestCase):
    def location(self, repo):
        return "git+" + str(repo) + "#" + REF

    def test_absent_subtree_is_never_a_genesis(self):
        repo = ledger(chain_files([receipt(repository="SV-011/.github")], "repository-ledger/SV-011/.github"))
        assert_six(self, rli.readback(MEMBER, self.location(repo), org()), "ENDPOINT_LEDGER_EMPTY_NOT_A_GENESIS")

    def pinned(self, genesis):
        contract = org()
        binding = contract["member_repository_ledgers"]["bindings"][0]
        binding["repository_ledger_location"]["expected_genesis_receipt_sha256"] = genesis
        binding["ledger_designation"]["expected_genesis_receipt_sha256"] = genesis
        return contract

    def test_chain_reads_back_to_genesis_and_writes_nothing(self):
        first = receipt()
        second = receipt(first["receipt_sha256"], 1)
        repo = ledger(chain_files([first, second]))
        tip = subprocess.run(["git", "-C", str(repo), "rev-parse", REF], capture_output=True, text=True).stdout
        unpinned = rli.readback(MEMBER, self.location(repo), org())
        assert_six(self, unpinned, "ORG_LEDGER_GENESIS_NOT_PINNED")
        self.assertEqual(unpinned["observed_genesis_receipt_sha256"], first["receipt_sha256"])
        allowed = rli.readback(MEMBER, self.location(repo), self.pinned(first["receipt_sha256"]))
        self.assertEqual((allowed["disposition"], allowed["chain_length"]), ("ALLOW", 2))
        assert_six(self, allowed["append_leg"], "REPOSITORY_LEDGER_INTAKE_NOT_PRESENT")
        wrong = rli.readback(MEMBER, self.location(repo), self.pinned("sha256:" + "0" * 64))
        assert_six(self, wrong, "ENDPOINT_LEDGER_GENESIS_MISMATCH")
        after = subprocess.run(["git", "-C", str(repo), "rev-parse", REF], capture_output=True, text=True).stdout
        self.assertEqual(tip, after)

    def test_tampered_chain_fails_closed(self):
        first = receipt()
        files = chain_files([first])
        key = next(k for k in files if "/receipts/" in k)
        files[key] = {**first, "sequence": 5}
        repo = ledger(files)
        assert_six(self, rli.readback(MEMBER, self.location(repo), org()), "ENDPOINT_LEDGER_UNREADABLE")

    def test_location_must_be_the_designated_ref(self):
        repo = ledger(chain_files([receipt()]))
        assert_six(self, rli.readback(MEMBER, "git+" + str(repo) + "#refs/heads/main", org()),
                   "READBACK_LOCATION_NOT_THE_DESIGNATED_REF")


class Boundaries(unittest.TestCase):
    def test_no_clock_network_or_431_import(self):
        text = (ROOT / "resident-runtime/repository_ledger_intake.py").read_text(encoding="utf-8")
        for word in ("import datetime", "import time", "urllib", "socket", "llm_adapter", ".put(", "append_transaction", "grant"):
            self.assertNotIn(word, text)

    def test_contract_declaration_words(self):
        block = json.dumps(org()["member_repository_ledgers"]).lower()
        for word in ("grant", "authority_effect", "kv ledger", "gateway node"):
            self.assertNotIn(word, block)


if __name__ == "__main__":
    unittest.main()
