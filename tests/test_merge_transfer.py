"""Main-merge transfer: append on push to main, and the verifier that makes a transfer VERIFIED.

The fixtures (tests/fixtures/merge-transfer/<merge8>.json) pin this
organization's real ledger tip and one real merge on main. The temp ledger is
that tip's tree (git archive of the pinned ledger commit), never the published
ref: the workflow that runs these tests has contents: read and pushes nothing.
The ledger commit and the merge commit must be in this clone (the validation
workflow fetches full history and the ledger ref); a missing object fails,
it does not skip.
"""
from __future__ import annotations

import copy
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
FIXTURES = sorted((ROOT / "tests/fixtures/merge-transfer").glob("*.json"))
WRITER = ROOT / ".github/workflows/organization-ledger-merge-transfer.yml"
VALIDATION = ROOT / ".github/workflows/organization-ledger-merge-transfer-validation.yml"
PENDING = ROOT / ".stegverse/merge-transfer/pending.json"


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mt = load("merge_transfer_under_test", "resident-runtime/merge_transfer.py")


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True).stdout


class Ledger:
    """A temp copy of the pinned ledger tip, bound through the writers' own location variables."""

    def __init__(self, fixture: dict, empty: bool = False):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        if not empty:
            subprocess.run("git -C '%s' archive %s | tar -x -C '%s'" % (ROOT, fixture["ledger_tip"], self.root),
                           shell=True, check=True)
        self.repo_root = self.root / fixture["repository_ledger_namespace"]
        self.saved = {k: os.environ.get(k) for k in ("STEGVERSE_ORG_LEDGER_ROOT", "STEGVERSE_REPO_LEDGER_ROOT")}
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = str(self.root)
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = str(self.repo_root)

    def files(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def read(self, key: str, repo: bool = False):
        return json.loads(((self.repo_root if repo else self.root) / key).read_text())

    def write(self, key: str, value, repo: bool = False):
        ((self.repo_root if repo else self.root) / key).write_text(json.dumps(value))

    def close(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.dir.cleanup()


def reseal(receipt: dict) -> dict:
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    return {**body, "receipt_sha256": mt.sha(body)}


class Fixtures(unittest.TestCase):
    def test_there_is_a_fixture_per_pending_merge(self):
        pending = json.loads(PENDING.read_text())
        self.assertEqual(pending["schema"], mt.PENDING_SCHEMA)
        self.assertEqual(pending["repository"], mt.repository())
        named = sorted(json.loads(p.read_text())["commit"]["merge_commit"] for p in FIXTURES)
        self.assertEqual(sorted(row["merge_commit"] for row in pending["merges"]), named)
        self.assertTrue(FIXTURES)

    def test_commit_facts_are_the_commit_object(self):
        for path in FIXTURES:
            fixture = json.loads(path.read_text())
            self.assertEqual(mt.commit_facts(fixture["commit"]["merge_commit"]), fixture["commit"], path.name)
            # on main: the merge is first-parent reachable from this checkout's main history
            self.assertIn(fixture["commit"]["merge_commit"], git("rev-list", "--first-parent", "HEAD").split())


class RealStateAndAppend(unittest.TestCase):
    def setUp(self):
        self.fixtures = [json.loads(p.read_text()) for p in FIXTURES]

    def test_real_state_at_the_ledger_tip_is_fail_closed_with_six_fields(self):
        for fixture in self.fixtures:
            ledger = Ledger(fixture)
            try:
                result = mt.verify_merge_transfer(fixture["repository"], fixture["commit"]["merge_commit"])
            finally:
                ledger.close()
            self.assertEqual(result, fixture["real_state_at_ledger_tip"])
            self.assertEqual(result["disposition"], "FAIL_CLOSED")
            self.assertEqual(result["failure_code"], mt.NOT_LEDGERED)
            self.assertEqual(result["transfer"], "UNVERIFIED")
            for field in mt.SIX_FIELDS:
                self.assertTrue(result[field], field)

    def test_the_append_writes_the_expected_receipts_and_the_transfer_verifies(self):
        for fixture in self.fixtures:
            ledger = Ledger(fixture)
            try:
                before = ledger.files()
                result = mt.append_merge_transfer(fixture["commit"])
                self.assertEqual(result, fixture["append_result"])
                key = mt.store_module().receipt_key
                org = ledger.read(key(result["organization_receipt_sha256"]))
                rep = ledger.read(key(result["repository_receipt_sha256"]), repo=True)
                self.assertEqual(org, fixture["expected_organization_receipt"])
                self.assertEqual(rep, fixture["expected_repository_receipt"])
                self.assertTrue(mt.sealed(org) and mt.sealed(rep))
                # links: the org receipt names the merge, and its repository receipt carries it
                self.assertEqual(mt._boundary(org)["merge_commit"], fixture["commit"]["merge_commit"])
                self.assertEqual(mt._boundary(org)["repository"], fixture["repository"])
                self.assertEqual(org["repo_receipt_sha256"], rep["receipt_sha256"])
                self.assertEqual(rep["evidence"]["merge_commit"], fixture["commit"]["merge_commit"])
                # the epoch is the head's supplied epoch, not a clock
                tip_head = before["HEAD.json"]
                head_receipt = json.loads(before[key(json.loads(tip_head)["receipt_sha256"])])
                self.assertEqual(org["hb_reference"]["epoch"], head_receipt["hb_reference"]["epoch"])
                self.assertIs(org["hb_reference"]["derived_from_clock"], False)
                # append-only: nothing that was there changed except the two HEADs
                after = ledger.files()
                for name, data in before.items():
                    if not name.endswith("HEAD.json"):
                        self.assertEqual(after[name], data, name)
                verified = mt.verify_merge_transfer(fixture["repository"], fixture["commit"]["merge_commit"])
                self.assertEqual(verified, fixture["verified"])
                self.assertEqual((verified["disposition"], verified["transfer"]), ("ALLOW", "VERIFIED"))
                # a replay is answered from the ledger: same receipts, nothing new
                replay = mt.append_merge_transfer(fixture["commit"])
                self.assertTrue(replay["replayed"])
                self.assertEqual(replay["organization_receipt_sha256"], result["organization_receipt_sha256"])
                self.assertEqual(ledger.files(), after)
            finally:
                ledger.close()

    def test_the_ingress_append_defaults_are_unchanged(self):
        import inspect
        signature = inspect.signature(mt.ingress._append)
        self.assertEqual(signature.parameters["operation"].default, mt.ingress.OPERATION_ID)
        self.assertEqual(signature.parameters["profile_id"].default, mt.ingress.PROFILE_ID)
        self.assertIsNone(signature.parameters["boundary_extra"].default)
        for name in ("operation", "profile_id", "boundary_extra"):
            self.assertEqual(signature.parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)


class Refusals(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURES[0].read_text())
        self.merge = self.fixture["commit"]["merge_commit"]
        self.repo = self.fixture["repository"]

    def _six(self, result, code):
        self.assertEqual(result["disposition"], "FAIL_CLOSED", result)
        self.assertEqual(result["failure_code"], code, result)
        for field in mt.SIX_FIELDS:
            self.assertTrue(result[field], field)

    def test_an_empty_ledger_has_no_epoch_source_and_nothing_is_written(self):
        ledger = Ledger(self.fixture, empty=True)
        try:
            self._six(mt.append_merge_transfer(self.fixture["commit"]), mt.EPOCH_UNOBSERVED)
            self.assertEqual(ledger.files(), {})
            self._six(mt.verify_merge_transfer(self.repo, self.merge), mt.NOT_LEDGERED)
        finally:
            ledger.close()

    def _head_receipt(self, ledger):
        key = mt.store_module().receipt_key(ledger.read("HEAD.json")["receipt_sha256"])
        return key, ledger.read(key)

    def test_a_clock_derived_or_unsealed_head_epoch_is_unobserved(self):
        for mutate in ("clock", "missing", "bool", "below_anchor", "unsealed"):
            ledger = Ledger(self.fixture)
            try:
                key, receipt = self._head_receipt(ledger)
                if mutate == "clock":
                    receipt["hb_reference"]["derived_from_clock"] = True
                elif mutate == "missing":
                    receipt["hb_reference"].pop("epoch")
                elif mutate == "bool":
                    receipt["hb_reference"]["epoch"] = True
                elif mutate == "below_anchor":
                    receipt["hb_reference"]["epoch"] = 31
                if mutate == "unsealed":
                    receipt["tampered"] = True  # the seal no longer verifies
                    ledger.write(key, receipt)
                else:
                    ledger.write(key, reseal(receipt))
                before = ledger.files()
                self._six(mt.append_merge_transfer(self.fixture["commit"]), mt.EPOCH_UNOBSERVED)
                self.assertEqual(ledger.files(), before, mutate)
            finally:
                ledger.close()

    def test_invalid_commit_facts_are_refused_before_any_read(self):
        for bad in ({}, {**self.fixture["commit"], "merge_commit": "d7beaf59"},
                    {**self.fixture["commit"], "parents": []},
                    {**self.fixture["commit"], "tree": None}):
            self._six(mt.append_merge_transfer(bad), mt.FACTS_INVALID)

    def test_one_merge_commit_binds_one_transfer_receipt(self):
        ledger = Ledger(self.fixture)
        try:
            self.assertEqual(mt.append_merge_transfer(self.fixture["commit"])["disposition"], "ALLOW")
            other = {**self.fixture["commit"], "subject": "a different subject"}
            before = ledger.files()
            self._six(mt.append_merge_transfer(other), mt.COLLISION)
            self.assertEqual(ledger.files(), before)
        finally:
            ledger.close()

    def _appended(self, ledger):
        result = mt.append_merge_transfer(self.fixture["commit"])
        key = mt.store_module().receipt_key
        return key, key(result["organization_receipt_sha256"]), key(result["repository_receipt_sha256"])

    def _rehead(self, ledger, org_key, org):
        """Reseal the head organization receipt under its new digest and point HEAD at it."""
        org = reseal(org)
        (ledger.root / org_key).unlink()
        ledger.write(mt.store_module().receipt_key(org["receipt_sha256"]), org)
        head = ledger.read("HEAD.json")
        ledger.write("HEAD.json", {**head, "receipt_sha256": org["receipt_sha256"]})

    def _deny(self, result):
        self.assertEqual(result["disposition"], "DENY", result)
        self.assertEqual(result["failure_code"], mt.LINK_UNVERIFIED, result)
        self.assertEqual(result["transfer"], "UNVERIFIED")
        for field in mt.SIX_FIELDS:
            self.assertTrue(result[field], field)

    def test_present_evidence_that_contradicts_the_merge_is_deny(self):
        """DENY MERGE_TRANSFER_RECEIPT_LINK_UNVERIFIED: sealed evidence that names another merge, repository, ref or
        schema, a digest it does not carry, or a link to the wrong repository receipt."""
        evidence_cases = {"other_merge": ("merge_commit", "0" * 40), "other_repository": ("repository", "SV-000/.github"),
                          "other_ref": ("ref", "refs/heads/feature"), "other_schema": ("schema", "stegverse.other/v1"),
                          "digest_not_carried": ("tree", "f" * 40)}
        for case, (field, value) in evidence_cases.items():
            ledger = Ledger(self.fixture)
            try:
                _, org_key, rep_key = self._appended(ledger)
                rep = ledger.read(rep_key, repo=True)
                rep["evidence"][field] = value
                ledger.write(rep_key, reseal(rep), repo=True)  # sealed, but sealed as another receipt
                result = mt.verify_merge_transfer(self.repo, self.merge)
                self._deny(result)
                self.assertIn(field if case != "digest_not_carried" else "digest", result["detail"], case)
            finally:
                ledger.close()
        for case in ("link_to_another_repository_receipt", "org_receipt_names_another_repository",
                     "repository_receipt_sealed_as_another"):
            ledger = Ledger(self.fixture)
            try:
                key, org_key, rep_key = self._appended(ledger)
                org = ledger.read(org_key)
                others = [p for p in (ledger.repo_root / "receipts").glob("*.json") if p.name != Path(rep_key).name]
                self.assertTrue(others)
                other = json.loads(others[0].read_text())
                self.assertTrue(mt.sealed(other))
                if case == "link_to_another_repository_receipt":
                    org["repo_receipt_sha256"] = other["receipt_sha256"]
                    self._rehead(ledger, org_key, org)
                elif case == "org_receipt_names_another_repository":
                    org["repository" if "repository" in org else "source_repository"] = "SV-000/.github"
                    self._rehead(ledger, org_key, org)
                else:
                    ledger.write(rep_key, other, repo=True)  # a sealed receipt stored under the linked digest
                self._deny(mt.verify_merge_transfer(self.repo, self.merge))
            finally:
                ledger.close()

    def test_absent_or_unsealed_evidence_is_fail_closed_not_ledgered(self):
        for case in ("repo_receipt_missing", "repo_receipt_unsealed", "org_receipt_unsealed", "other_repository",
                     "other_merge"):
            ledger = Ledger(self.fixture)
            try:
                _, org_key, rep_key = self._appended(ledger)
                if case == "repo_receipt_missing":
                    (ledger.repo_root / rep_key).unlink()
                elif case == "repo_receipt_unsealed":
                    rep = ledger.read(rep_key, repo=True)
                    rep["evidence"]["merge_commit"] = "0" * 40  # not resealed
                    ledger.write(rep_key, rep, repo=True)
                elif case == "org_receipt_unsealed":
                    org = ledger.read(org_key)
                    org["hb_reference"]["epoch"] += 1
                    ledger.write(org_key, org)
                repo = "StegVerse-Labs/.github" if case == "other_repository" else self.repo
                merge = "1" * 40 if case == "other_merge" else self.merge
                result = mt.verify_merge_transfer(repo, merge)
                self._six(result, mt.NOT_LEDGERED)
                self.assertEqual(result["transfer"], "UNVERIFIED")
            finally:
                ledger.close()


class PushedCommits(unittest.TestCase):
    def test_pending_first_then_the_first_parent_range(self):
        fixture = json.loads(FIXTURES[0].read_text())
        merge = fixture["commit"]["merge_commit"]
        first_parent = fixture["commit"]["parents"][0]
        self.assertEqual(mt.pushed_commits(first_parent, merge, None), [merge])
        self.assertEqual(mt.pushed_commits("0" * 40, merge, None), [merge])
        self.assertEqual(mt.pushed_commits(first_parent, merge, PENDING), [merge])
        head = git("rev-parse", "HEAD").strip()
        listed = mt.pushed_commits(merge, head, PENDING)
        self.assertEqual(listed[0], merge)
        self.assertEqual(listed[1:], git("rev-list", "--first-parent", "--reverse", merge + ".." + head).split())

    def test_a_pending_merge_not_on_main_is_refused(self):
        fixture = json.loads(FIXTURES[0].read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.json"
            path.write_text(json.dumps({"schema": mt.PENDING_SCHEMA, "repository": mt.repository(),
                                        "merges": [{"merge_commit": fixture["commit"]["merge_commit"]}]}))
            with self.assertRaises(SystemExit):
                mt.pushed_commits(None, fixture["commit"]["parents"][0], path)


class WorkflowShape(unittest.TestCase):
    """A merge on main triggers the append; nothing else does, and nothing on a PR writes."""

    def test_the_writer_runs_only_on_push_to_main(self):
        text = WRITER.read_text()
        on = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(on.strip(), "push:\n    branches: [main]")
        for word in ("workflow_dispatch", "schedule", "cron", "repository_dispatch", "workflow_run",
                     "pull_request", "paths"):
            self.assertNotIn(word, on, word)
        self.assertNotIn("secrets.", text)
        self.assertIn("\npermissions:\n  contents: read\n", text)
        self.assertIn("    permissions:\n      contents: write\n", text)
        self.assertIn("concurrency:\n  group: organization-ledger\n  cancel-in-progress: false\n", text)
        self.assertIn('git push origin "$LEDGER_REF:$LEDGER_REF"', text)
        self.assertNotRegex(text, r"git push[^\n]*\+")
        self.assertIn("merge_transfer.py verify", text)

    def test_the_validation_workflow_is_read_only_and_never_pushes(self):
        text = VALIDATION.read_text()
        on = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertTrue(on.lstrip().startswith("pull_request:"))
        for word in ("push:", "workflow_dispatch", "schedule", "repository_dispatch"):
            self.assertNotIn(word, on)
        self.assertIn("\npermissions:\n  contents: read\n", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("if:", text)  # every job and step runs; nothing is conditionally skipped


if __name__ == "__main__":
    unittest.main()
