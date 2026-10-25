"""The public-ledger route of the cross-org read (E0, no credential): a carried source receipt verified on an
anonymously readable source ledger. (Identical in StegVerse-org/.github and StegVerse-Labs/.github.)

Deterministic and offline: the "anonymous copy" is a temporary bare repository built from this repository's
committed inbound row (.stegverse/intr-path-transfer/inbound.json), whose carried source receipts are byte-for-value
copies of the authentic, sealed receipts on the source ledger. The copy's HEAD.json names the carried organization
receipt, so the chain is that one sealed receipt. Fixed commit dates; no network, clock or credential. The live
anonymous fetch and dry run against the real source ledger are the validation workflow's public-ledger step.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INBOUND = json.loads((ROOT / ".stegverse/intr-path-transfer/inbound.json").read_text(encoding="utf-8"))
ROW = INBOUND["transfers"][0]
PID, CID = ROW["path_id"], ROW["communication_id"]
SOURCE_ORG, DESTINATION_ORG = PID.split(":")[0].split("->")
SOURCE_REPOSITORY = SOURCE_ORG + "/.github"
ORG_RECEIPT = ROW["carried"]["source_organization_receipt"]
REPO_RECEIPT = ROW["carried"]["source_repository_receipt"]
SIX = ("failure_code", "failed_predicate", "required_evidence_or_repair", "retry_entrypoint",
       "owning_existing_goal", "next_attempt")
FIXED = {"GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@invalid", "GIT_AUTHOR_DATE": "@0 +0000",
         "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@invalid", "GIT_COMMITTER_DATE": "@0 +0000"}


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pi = load("intr_path_ingress_public_route", "resident-runtime/intr_path_ingress.py")


def _git(bare: Path, *args: str, data: bytes | None = None) -> str:
    env = {**os.environ, **FIXED}
    return subprocess.run(["git", "--git-dir=" + str(bare), *args], input=data, capture_output=True,
                          check=True, env=env).stdout.decode().strip()


def build_copy(root: Path) -> str:
    """A bare <SOURCE_ORG>.git whose ledger ref holds HEAD.json -> the carried receipts. Returns the tip."""
    bare = root / (SOURCE_ORG + ".git")
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)

    def blob(doc) -> str:
        return _git(bare, "hash-object", "-w", "--stdin", data=(json.dumps(doc, indent=2, sort_keys=True) + "\n").encode())

    def tree(entries: dict[str, str]) -> str:
        lines = "".join(("100644 blob " if kind == "blob" else "040000 tree ") + sha + "\t" + name + "\n"
                        for name, (kind, sha) in sorted(entries.items()))
        return _git(bare, "mktree", data=lines.encode())

    org_key = ORG_RECEIPT["receipt_sha256"].split(":", 1)[1] + ".json"
    repo_key = REPO_RECEIPT["receipt_sha256"].split(":", 1)[1] + ".json"
    repo_tree = tree({"receipts": ("tree", tree({repo_key: ("blob", blob(REPO_RECEIPT))}))})
    top = tree({
        "HEAD.json": ("blob", blob({"receipt_sha256": ORG_RECEIPT["receipt_sha256"]})),
        "receipts": ("tree", tree({org_key: ("blob", blob(ORG_RECEIPT))})),
        "repository-ledger": ("tree", tree({SOURCE_ORG: ("tree", tree({".github": ("tree", repo_tree)}))})),
    })
    tip = _git(bare, "commit-tree", top, "-m", "fixture ledger copy")
    _git(bare, "update-ref", pi.LEDGER_REF, tip)
    return tip


class PublicLedgerRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tip = build_copy(self.root)
        self.checked = {"path_id": PID, "communication_id": CID, "source_repository": SOURCE_REPOSITORY,
                        "source_organization_receipt_sha256": ORG_RECEIPT["receipt_sha256"],
                        "source_repository_receipt_sha256": REPO_RECEIPT["receipt_sha256"]}
        self.no_tvc = self.root / "no-tvc-receipts"

    def test_carried_row_is_for_this_destination(self):
        self.assertEqual(INBOUND["destination_repository"], DESTINATION_ORG + "/.github")
        self.assertEqual(ROW["source_ledger_tip_observed"], ROW["source_ledger_tip_observed"].lower())

    def test_public_readback_reads_the_carried_sealed_pair(self):
        rb = pi.public_readback(PID, CID, SOURCE_REPOSITORY, self.root)
        self.assertEqual((rb["schema"], rb["repository"], rb["ledger_tip"], rb["read_route"]),
                         (pi.READBACK_SCHEMA, SOURCE_REPOSITORY, self.tip, "ANONYMOUS_PUBLIC_LEDGER_READ"))
        self.assertEqual(rb["organization_receipt"], ORG_RECEIPT)
        self.assertEqual(rb["repository_receipt"], REPO_RECEIPT)
        self.assertTrue(pi._included(rb, rb["organization_receipt"]))
        checked = pi.check_source_pair(rb["organization_receipt"], rb["repository_receipt"], PID, CID, DESTINATION_ORG)
        self.assertEqual(checked["disposition"], "ALLOW", checked)
        self.assertEqual(pi.public_readback(PID, CID, SOURCE_REPOSITORY, self.root), rb)   # deterministic

    def test_source_inclusion_allows_without_any_credential(self):
        got = pi.source_inclusion(self.checked, self.no_tvc, self.root)
        self.assertEqual((got["disposition"], got["state"], got["source_ledger_tip"], got["tvc_read_receipt"]),
                         ("ALLOW", pi.PUBLIC_STATE, self.tip, None))

    def test_contradicted_copy_is_deny_with_six_fields(self):
        got = pi.source_inclusion({**self.checked, "source_repository_receipt_sha256": "sha256:" + "0" * 64},
                                  self.no_tvc, self.root)
        self.assertEqual((got["disposition"], got["failure_code"]), ("DENY", pi.SOURCE_LINK_UNVERIFIED))
        self.assertTrue(all(got[f] for f in SIX))

    def test_no_anonymous_copy_falls_back_to_the_tvc_route_fail_closed(self):
        for public in (self.root / "absent", self.root / "none"):
            with self.subTest(public=public):
                got = pi.source_inclusion(self.checked, self.no_tvc, public)
                self.assertEqual((got["disposition"], got["failure_code"]), ("FAIL_CLOSED", pi.CREDENTIAL_NOT_ISSUED))
                self.assertTrue(all(got[f] for f in SIX))

    def test_unsafe_organization_names_are_not_resolved(self):
        for other in ("../x/.github", "/.github", ".hidden/.github", ""):
            self.assertIsNone(pi.public_ledger_dir(other, self.root))

    def test_end_to_end_is_terminal_at_e0(self):
        # No destination ingress receipt yet: terminal FAIL_CLOSED with six fields, never waiting.
        src = pi.public_readback(PID, CID, SOURCE_REPOSITORY, self.root)
        dst = {"schema": pi.READBACK_SCHEMA, "repository": DESTINATION_ORG + "/.github", "ledger_tip": None,
               "head_receipt_sha256": None, "chain": [], "organization_receipt": None, "repository_receipt": None}
        got = pi.verify_end_to_end(PID, CID, src, dst)
        self.assertEqual((got["disposition"], got["failure_code"], got["half"]),
                         ("FAIL_CLOSED", pi.NOT_LEDGERED, "end_to_end"))
        self.assertTrue(all(got[f] for f in SIX))


if __name__ == "__main__":
    unittest.main()
