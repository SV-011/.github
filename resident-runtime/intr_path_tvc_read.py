#!/usr/bin/env python3
"""InTr path: the other organization's ledger, read through the TV/TVC issuance transition.

The other half of a path lives in another organization's internal .github
ledger. This organization's GITHUB_TOKEN reads only this repository; no classic
PAT, FEDERATION_HOP_TOKEN or FEDERATION_READ_TOKEN is used, and no bot holds a
credential. The read is TVC's: StegVerse-Labs/TVC TVC-PRIVATE-SOURCE-READ-001
(tvc.private-source-read.v1, TV policy c6eede49) with the InTr path consumer
scripts/run_intr_path_ledger_reads.py (manifest
data/intr-path-ledger-read/v1/manifest.json: one scope per path and purpose,
contents:read on one ledger ref). TVC writes a
stegverse.tvc-intr-path-ledger-read-receipt/v1 carrying a
stegverse.intr-path-ledger-readback/v1; landing that receipt byte for byte at
.stegverse/intr-path-transfer/tvc-read-receipts/ on this repository's main is
the manifest transition that makes it observable here.

``lookup`` answers with the readback only when the receipt verifies:
  - its own digest and its readback's digest recompute;
  - capability, policy pin, consumer task, ref, contents:read and read-only flags are TVC's;
  - it is for this path, this purpose, this reader organization and the other organization's repository;
  - authorized = observed = requested ledger tip = the readback's ledger_tip, and the head epoch is supplied;
  - its evidence class is TVC_RESIDENT_EXECUTION (fixtures only when a test asks).
Absent: FAIL_CLOSED INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED, retry at the
TVC transition (not at an owner). A TVC non-ALLOW receipt keeps that code and
carries TVC's own six fields as detail. A contradicted receipt is DENY
INTR_PATH_TVC_READ_RECEIPT_CONTRADICTED. Deterministic: no clock, no network.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
RECEIPT_DIR = ROOT / ".stegverse" / "intr-path-transfer" / "tvc-read-receipts"
RECEIPT_SCHEMA = "stegverse.tvc-intr-path-ledger-read-receipt/v1"
READBACK_SCHEMA = "stegverse.intr-path-ledger-readback/v1"
CAPABILITY_ID = "tvc.private-source-read.v1"
POLICY_COMMIT = "c6eede495c18b8b19156cf68576702b93e77f593"
CONSUMER_TASK = "INTR-PATH-LEDGER-READBACK-001"
LEDGER_REF = "refs/stegverse/organization-ledger"
PURPOSES = {"source_inclusion": "INTR_MESH_PATH_TRANSFER",
            "destination_readback": "ORGANIZATION_INTR_DESTINATION_INGRESS"}
RESIDENT = "TVC_RESIDENT_EXECUTION"
FIXTURE = "FIXTURE_NOT_AUTHENTIC"
MIN_EPOCH = 32
TVC_TRANSITION = "StegVerse-Labs/TVC TVC-PRIVATE-SOURCE-READ-001 (tvc.private-source-read.v1, TV policy c6eede49)"
TVC_GOAL = "TVC-PRIVATE-SOURCE-READ-001 (StegVerse-Labs/TVC#33; consumer INTR-PATH-LEDGER-READBACK-001, StegVerse-Labs/TVC#528)"
NOT_ISSUED = "INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED"
CONTRADICTED = "INTR_PATH_TVC_READ_RECEIPT_CONTRADICTED"
NOT_AUTHENTIC = "INTR_PATH_TVC_READ_RECEIPT_NOT_RESIDENT"
SIX_FIELDS = ("failure_code", "failed_predicate", "required_evidence_or_repair", "retry_entrypoint",
              "owning_existing_goal", "next_attempt")
ALLOW, DENY, FAIL_CLOSED = "ALLOW", "DENY", "FAIL_CLOSED"


def sha256(value: Any) -> str:
    """TVC's receipt digest (scripts/run_intr_path_ledger_reads.py sha256): hex of canonical JSON."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def scope_id(path_id: str, purpose: str) -> str:
    return path_id + "#" + purpose


def receipt_file(path_id: str, purpose: str, directory: Path | str = RECEIPT_DIR) -> Path:
    ends = path_id.split(":")[0].split("->")
    return Path(directory) / (ends[0] + "--" + (ends[1] if len(ends) > 1 else "") + "." + purpose + ".json")


def retry_entrypoint(path_id: str, purpose: str, read_repository: str) -> str:
    return ("StegVerse-Labs/TVC: python scripts/run_intr_path_ledger_reads.py request --path-id '" + path_id
            + "' --purpose " + purpose + " --ledger-tip <observed tip of " + read_repository + " " + LEDGER_REF
            + "> -> existing stegtvc-private-source-read.path/.service (" + TVC_TRANSITION
            + ") -> run_intr_path_ledger_reads.py collect; land the receipt at .stegverse/intr-path-transfer/"
            "tvc-read-receipts/" + receipt_file(path_id, purpose, ".").name)


def _refusal(disposition: str, code: str, predicate: str, repair: str, next_attempt: str, path_id: str, purpose: str,
             read_repository: str, **extra: Any) -> dict[str, Any]:
    return {"disposition": disposition, "failure_code": code, "failed_predicate": predicate,
            "required_evidence_or_repair": repair, "retry_entrypoint": retry_entrypoint(path_id, purpose, read_repository),
            "owning_existing_goal": TVC_GOAL, "next_attempt": next_attempt, "path_id": path_id,
            "purpose": purpose, "read_repository": read_repository, "classification": "A", **extra}


def not_issued(path_id: str, purpose: str, read_repository: str, **extra: Any) -> dict[str, Any]:
    return _refusal(FAIL_CLOSED, NOT_ISSUED, "OTHER_ORGANIZATION_LEDGER_IS_READ_THROUGH_TV_TVC",
                    "a stegverse.tvc-intr-path-ledger-read-receipt/v1 from the TVC resident (" + TVC_TRANSITION
                    + ") for scope " + scope_id(path_id, purpose) + ": contents:read on " + read_repository + " "
                    + LEDGER_REF + ", landed at .stegverse/intr-path-transfer/tvc-read-receipts/"
                    + receipt_file(path_id, purpose, ".").name + ". GITHUB_TOKEN reads only this repository; "
                    "no classic PAT or FEDERATION_* token is used; no bot holds a credential",
                    "once the TVC grant receipt is observed here, re-run the verifier; nothing was verified",
                    path_id, purpose, read_repository, **extra)


def verify_receipt(receipt: Any, path_id: str, purpose: str, reader_organization: str, read_repository: str,
                   *, allow_fixture: bool = False) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(readback, None) when the TVC receipt verifies for this scope, else (None, six-field refusal)."""
    if not isinstance(receipt, Mapping):
        return None, not_issued(path_id, purpose, read_repository)
    if receipt.get("schema") != RECEIPT_SCHEMA:
        return None, _refusal(DENY, CONTRADICTED, "TVC_READ_RECEIPT_HAS_THE_TVC_SCHEMA", "a TVC read receipt",
                              "inspect the landed receipt; nothing was verified", path_id, purpose, read_repository,
                              detail="schema " + repr(receipt.get("schema")))
    problems = []
    if receipt.get("receipt_sha256") != sha256({k: v for k, v in receipt.items() if k != "receipt_sha256"}):
        problems.append("receipt_sha256 does not recompute")
    want = {"capability_id": CAPABILITY_ID, "policy_commit_sha": POLICY_COMMIT, "consumer_task": CONSUMER_TASK,
            "exact_ref": LEDGER_REF, "contents_scope": "contents:read", "scope_id": scope_id(path_id, purpose),
            "path_id": path_id, "purpose": purpose, "operation_read": PURPOSES.get(purpose),
            "reader_organization": reader_organization, "read_repository": read_repository}
    for key, value in want.items():
        if receipt.get(key) != value:
            problems.append(key + " is " + repr(receipt.get(key)))
    for flag in ("lane_credential_used", "credential_value_exposed", "write_authority", "release_authority",
                 "publication_authority", "runtime_authority"):
        if receipt.get(flag) is not False:
            problems.append(flag + " is not false")
    if problems:
        return None, _refusal(DENY, CONTRADICTED, "TVC_READ_RECEIPT_IS_FOR_THIS_SCOPE_AND_RECOMPUTES",
                              "a TVC read receipt for exactly this scope whose digests recompute",
                              "inspect the landed receipt; nothing was verified", path_id, purpose, read_repository,
                              detail="; ".join(problems))
    if receipt.get("disposition") != ALLOW:
        return None, not_issued(path_id, purpose, read_repository,
                                tvc_receipt_sha256=receipt.get("receipt_sha256"),
                                tvc_refusal={f: receipt.get(f) for f in ("disposition",) + SIX_FIELDS})
    if receipt.get("evidence_class") != RESIDENT and not (allow_fixture and receipt.get("evidence_class") == FIXTURE):
        return None, _refusal(FAIL_CLOSED, NOT_AUTHENTIC, "TVC_READ_RECEIPT_IS_A_RESIDENT_EXECUTION",
                              "a receipt the TVC resident wrote (evidence_class " + RESIDENT + "); fixtures are for tests",
                              "land the resident receipt; nothing was verified", path_id, purpose, read_repository,
                              evidence_class=receipt.get("evidence_class"))
    rb = receipt.get("readback")
    tip = receipt.get("requested_ledger_tip")
    epoch = receipt.get("ledger_head_epoch")
    if not isinstance(rb, Mapping) or rb.get("schema") != READBACK_SCHEMA \
            or receipt.get("readback_sha256") != sha256(rb) \
            or not (isinstance(tip, str) and tip == receipt.get("authorized_exact_sha") == receipt.get("observed_exact_sha")
                    == rb.get("ledger_tip")) \
            or rb.get("repository") != read_repository \
            or rb.get("head_receipt_sha256") != receipt.get("ledger_head_receipt_sha256") \
            or not (isinstance(epoch, int) and not isinstance(epoch, bool) and epoch >= MIN_EPOCH
                    and rb.get("ledger_head_epoch") == epoch) \
            or receipt.get("execution_state") != "COMPLETE":
        return None, _refusal(DENY, CONTRADICTED, "TVC_READ_RECEIPT_BINDS_ITS_READBACK",
                              "authorized = observed = requested tip = readback ledger_tip, the readback's digest, "
                              "repository and head, a COMPLETE execution and a supplied head epoch",
                              "inspect the landed receipt; nothing was verified", path_id, purpose, read_repository)
    return dict(rb), None


def lookup(path_id: str, purpose: str, reader_organization: str, read_repository: str, *,
           directory: Path | str = RECEIPT_DIR, allow_fixture: bool = False) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    path = receipt_file(path_id, purpose, directory)
    if not path.is_file():
        return None, not_issued(path_id, purpose, read_repository)
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None, _refusal(DENY, CONTRADICTED, "TVC_READ_RECEIPT_IS_JSON", "a JSON TVC read receipt",
                              "inspect the landed receipt; nothing was verified", path_id, purpose, read_repository)
    return verify_receipt(receipt, path_id, purpose, reader_organization, read_repository, allow_fixture=allow_fixture)
