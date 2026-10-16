#!/usr/bin/env python3
"""Append the organization receipt that consumes one repository receipt.

The root comes from ``STEGVERSE_ORG_LEDGER_ROOT``. A missing root fails
closed. The organization receipt preserves the source receipt digest and the
source receipt bytes under ``source-receipts/``. It does not select admission,
routing, or execution. Authority and time stay constraint variables. The
disposition on the evidence is transition evidence.

Admission follows the contract ``consumes`` list. The predecessor state is an
explicit sha256 digest, ``FROM_HEAD``, or ``GENESIS``. ``FROM_HEAD`` binds the
HEAD of this attempt. ``GENESIS`` is accepted only while HEAD is absent.

A receipt written here is not an authentic production receipt.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
C = json.loads((ROOT / ".stegverse/transition-ledger/org-contract.json").read_text(encoding="utf-8"))

_kernel_spec = importlib.util.spec_from_file_location("kernel", ROOT / "org-kernel/kernel.py")
kernel = importlib.util.module_from_spec(_kernel_spec)
_kernel_spec.loader.exec_module(kernel)

_store_spec = importlib.util.spec_from_file_location(
    "ledger_store", ROOT / "resident-runtime/ledger_store.py")
ledger_store = importlib.util.module_from_spec(_store_spec)
_store_spec.loader.exec_module(ledger_store)

HEAD_KEY = ledger_store.HEAD_KEY
RECEIPT_PREFIX = ledger_store.RECEIPT_PREFIX
SOURCE_PREFIX = ledger_store.SOURCE_PREFIX
open_store = ledger_store.open_store
parse_locator = ledger_store.parse_locator
receipt_key = ledger_store.receipt_key
source_key = ledger_store.source_key

SHA256_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
FROM_HEAD = "FROM_HEAD"
GENESIS = "GENESIS"
CURRENT_STATE_FIELDS = (
    "state_id",
    "as_of",
    "fresh_until",
    "declared_transition_element",
    "declared_role_escalation",
    "satisfied_scope_ids",
)
UTC_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(value: Any) -> str:
    raw = value if isinstance(value, (bytes, bytearray)) else canon(value)
    return "sha256:" + hashlib.sha256(bytes(raw)).hexdigest()


class LedgerLocationRequired(ValueError):
    """No ledger root was supplied; nothing is appended and nothing is derived."""

    failed_predicate = "LEDGER_LOCATION_REQUIRED_FROM_CALLER"

    def __init__(self, variable: str) -> None:
        super().__init__("ledger_location_required_from_caller: " + variable)
        self.variable = variable


class OrgLedgerAppendRefused(SystemExit):
    """A refused append. Nothing is written when this is raised."""

    retry_entrypoint = "resident-runtime/aggregate_repo_transition.py::append"

    def __init__(self, failed_predicate: str) -> None:
        super().__init__(failed_predicate)
        self.failed_predicate = failed_predicate


def require_state_digest(field: str, value: str) -> str:
    if not isinstance(value, str) or not SHA256_REF.match(value):
        raise SystemExit("ORG_LEDGER_" + field.upper() + "_NOT_A_SHA256_DIGEST")
    return value


def ledger_root():
    configured = os.getenv("STEGVERSE_ORG_LEDGER_ROOT")
    if not configured:
        raise LedgerLocationRequired("STEGVERSE_ORG_LEDGER_ROOT")
    return parse_locator(configured)


def load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize_current_state(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Copy only the six current-state fields a manifest actually declared.

    A partial object is refused. ``declared_transition_element`` and
    ``declared_role_escalation`` are not filled in when the manifest omits
    them. ``as_of`` and ``fresh_until`` must be ``YYYY-MM-DDTHH:MM:SSZ``. A
    caller-supplied digest is not stored; readback computes it.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("current_state_must_be_an_object")
    missing = [name for name in CURRENT_STATE_FIELDS if name not in value or value[name] is None]
    if missing:
        raise ValueError("current_state_fields_absent:" + ",".join(missing))
    if not isinstance(value["satisfied_scope_ids"], list):
        raise ValueError("satisfied_scope_ids_must_be_a_list")
    if any(not isinstance(item, str) or not item for item in value["satisfied_scope_ids"]):
        raise ValueError("satisfied_scope_ids_must_be_non_empty_strings")
    for name in ("state_id", "declared_transition_element", "declared_role_escalation"):
        if not isinstance(value[name], str) or not value[name]:
            raise ValueError("current_state_field_must_be_a_non_empty_string:" + name)
    for name in ("as_of", "fresh_until"):
        if not isinstance(value[name], str) or UTC_TIMESTAMP.fullmatch(value[name]) is None:
            raise ValueError("current_state_timestamp_not_yyyy_mm_ddthh_mm_ssz:" + name)
    return {name: value[name] for name in CURRENT_STATE_FIELDS}


def verify_required_evidence(receipt: Mapping[str, Any]) -> None:
    """Require exact inline canonical evidence bytes for organization-local replay."""
    manifest = receipt.get("required_evidence_manifest")
    if not isinstance(manifest, list):
        raise SystemExit("canonical required evidence manifest missing")
    seen: set[str] = set()
    for item in manifest:
        if not isinstance(item, dict):
            raise SystemExit("canonical evidence entry invalid")
        for key in ("evidence_id", "evidence_type", "origin_transition_id",
                    "encoding", "sha256", "content"):
            if key not in item:
                raise SystemExit("canonical evidence field missing: " + key)
        identity = item["evidence_id"]
        if not isinstance(identity, str) or not identity or identity in seen:
            raise SystemExit("canonical evidence identity invalid")
        seen.add(identity)
        if (not isinstance(item["evidence_type"], str) or not item["evidence_type"]
                or item["origin_transition_id"] != receipt.get("transition_id")):
            raise SystemExit("canonical evidence transition binding invalid")
        encoding = item["encoding"]
        content = item["content"]
        if encoding == "canonical-json":
            raw = canon(content)
        elif encoding == "utf-8" and isinstance(content, str):
            raw = content.encode("utf-8")
        elif encoding == "base64" and isinstance(content, str):
            try:
                raw = base64.b64decode(content.encode("ascii"), validate=True)
            except (ValueError, UnicodeError) as exc:
                raise SystemExit("canonical evidence base64 invalid") from exc
        else:
            raise SystemExit("canonical evidence encoding invalid")
        digest = item["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or hashlib.sha256(raw).hexdigest() != digest:
            raise SystemExit("canonical required evidence digest mismatch")


def verify_source(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a source transition receipt this organization's contract consumes."""
    schema = receipt.get("schema")
    allowed = C.get("consumes")
    if isinstance(allowed, str):
        allowed = [allowed]
    if schema not in (allowed or []):
        raise SystemExit("organization source receipt schema mismatch")
    transition_id = receipt.get("transition_id")
    if not isinstance(transition_id, str) or not transition_id:
        raise SystemExit("organization source transition id invalid")
    if schema == "stegverse.repo-transition-receipt/v1":
        repository = str(receipt.get("repository", ""))
        if not repository.startswith(C["organization"] + "/"):
            raise SystemExit("repo outside organization")
        claimed = receipt.get("receipt_sha256")
        body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        if claimed != sha(body):
            raise SystemExit("repo receipt hash mismatch")
        return {
            "source_receipt_schema": schema,
            "source_transition_sha256": claimed,
            "source_transition_id": transition_id,
            "source_repository": repository,
            "repo_receipt_sha256": claimed,
            "repo_transition_id": transition_id,
            "canonical_state_transition_receipt_sha256": None,
            "subject_or_correlation_id": None,
        }
    verify_required_evidence(receipt)
    digest = sha(receipt)
    return {
        "source_receipt_schema": schema,
        "source_transition_sha256": digest,
        "source_transition_id": transition_id,
        "source_repository": None,
        "repo_receipt_sha256": None,
        "repo_transition_id": None,
        "canonical_state_transition_receipt_sha256": digest,
        "subject_or_correlation_id": receipt.get("subject_or_correlation_id"),
    }


def _validate_existing_head(store: ledger_store.Store) -> str | None:
    head = store.get(HEAD_KEY)
    if head is None:
        if store.list_prefix(RECEIPT_PREFIX):
            raise SystemExit("ORG_LEDGER_HEAD_MISSING_WITH_EXISTING_RECEIPTS")
        return None
    previous = head.get("receipt_sha256") if isinstance(head, dict) else None
    if not isinstance(previous, str) or not previous.startswith("sha256:"):
        raise SystemExit("ORG_LEDGER_HEAD_INVALID")
    receipt = store.get(receipt_key(previous))
    if receipt is None:
        raise SystemExit("ORG_LEDGER_HEAD_RECEIPT_MISSING")
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if receipt.get("receipt_sha256") != previous or sha(body) != previous:
        raise SystemExit("ORG_LEDGER_HEAD_RECEIPT_HASH_MISMATCH")
    reachable: set[str] = set()
    cursor: str | None = previous
    while cursor:
        if cursor in reachable:
            raise SystemExit("ORG_LEDGER_PREDECESSOR_CYCLE")
        reachable.add(cursor)
        record = store.get(receipt_key(cursor))
        if record is None:
            raise SystemExit("ORG_LEDGER_PREDECESSOR_MISSING")
        record_body = {key: value for key, value in record.items() if key != "receipt_sha256"}
        if record.get("receipt_sha256") != cursor or sha(record_body) != cursor:
            raise SystemExit("ORG_LEDGER_PREDECESSOR_HASH_MISMATCH")
        cursor = record.get("previous_receipt_sha256")
    if store.list_prefix(RECEIPT_PREFIX) != {receipt_key(digest) for digest in reachable}:
        raise SystemExit("ORG_LEDGER_UNPUBLISHED_OR_ORPHAN_RECEIPTS_RECOVERY_REQUIRED")
    sources = set()
    for digest in reachable:
        named = store.get(receipt_key(digest)).get("source_transition_sha256")
        if isinstance(named, str):
            sources.add(source_key(named))
    if not store.list_prefix(SOURCE_PREFIX) <= sources:
        raise SystemExit("ORG_LEDGER_ORPHAN_SOURCE_RECEIPTS_RECOVERY_REQUIRED")
    return previous


def recorded(store: ledger_store.Store, head: Mapping[str, Any] | None,
             source_transition_sha256: str) -> dict[str, Any] | None:
    """The organization receipt this chain already holds for a source receipt."""
    cursor = (head or {}).get("receipt_sha256") if isinstance(head, Mapping) else None
    while cursor:
        receipt = store.get(receipt_key(cursor))
        if not isinstance(receipt, dict):
            return None
        if receipt.get("source_transition_sha256") == source_transition_sha256:
            return receipt
        cursor = receipt.get("previous_receipt_sha256")
    return None


def append(source_receipt: Mapping[str, Any], org_transition_class: str,
           predecessor_state: str, successor_state: str, boundary_evidence: Mapping[str, Any],
           authority_constraint: str, hb_epoch: int | None = None,
           store: ledger_store.Store | None = None, idempotent: bool = False,
           current_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = verify_source(source_receipt)
    if predecessor_state not in (FROM_HEAD, GENESIS):
        require_state_digest("predecessor_state_sha256", predecessor_state)
    require_state_digest("successor_state_sha256", successor_state)
    declared_state = normalize_current_state(current_state)
    retained = {source_key(source["source_transition_sha256"]): dict(source_receipt)}
    heartbeat = kernel.hb_reference(epoch=hb_epoch) if hb_epoch is not None else kernel.hb_reference()
    target = store or open_store(ledger_root())
    target.initialize()
    for _attempt in range(128):
        guard = target.exclusive() if hasattr(target, "exclusive") else nullcontext()
        with guard:
            expected_head = target.get(HEAD_KEY)
            head = expected_head if isinstance(expected_head, dict) else None
            previous = (head or {}).get("receipt_sha256")
            _validate_existing_head(target)
            if idempotent:
                prior = recorded(target, head, source["source_transition_sha256"])
                if prior is not None:
                    if (prior.get("org_transition_class") != org_transition_class
                            or prior.get("current_state") != declared_state):
                        raise ValueError("ledger_receipt_collision")
                    return prior
            if predecessor_state == FROM_HEAD:
                if head is None:
                    raise OrgLedgerAppendRefused("ORG_LEDGER_GENESIS_NOT_DECLARED")
                predecessor = previous
            elif predecessor_state == GENESIS:
                if head is not None:
                    raise OrgLedgerAppendRefused("ORG_LEDGER_GENESIS_ON_NON_EMPTY_LEDGER")
                predecessor = None
            else:
                predecessor = predecessor_state
            body: dict[str, Any] = {
                "schema": "stegverse.organization-transition-receipt/v1",
                "organization": C["organization"],
                "repository": C["repository"],
                **source,
                "org_transition_class": org_transition_class,
                "predecessor_state_sha256": predecessor,
                "successor_state_sha256": successor_state,
                "predecessor_org_state_sha256": predecessor,
                "successor_org_state_sha256": successor_state,
                "evidence": dict(boundary_evidence),
                "authority_constraint": authority_constraint,
                "time_constraint": "HEARTBEAT_EPOCH",
                "disposition_is_transition_evidence": True,
                "production_receipt_claimed": False,
                "hb_reference": heartbeat,
                "previous_receipt_sha256": previous,
            }
            if declared_state is not None:
                body["current_state"] = declared_state
            if predecessor_state == GENESIS:
                body["chain_genesis"] = True
            digest = sha(body)
            receipt = {**body, "receipt_sha256": digest}
            key = receipt_key(digest)
            new_head = {
                "schema": "stegverse.ledger-head/v1",
                "organization": C["organization"],
                "receipt_sha256": digest,
                "receipt_path": target.locator(key),
            }
        if target.append_transaction(key, receipt, expected_head, new_head, immutable=retained):
            return receipt
    raise SystemExit("ORG_LEDGER_APPEND_CONTENTION_EXHAUSTED")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-receipt")
    parser.add_argument("--transition-receipt")
    parser.add_argument("--org-transition-class", default=None)
    parser.add_argument("--predecessor-state-sha256", required=True)
    parser.add_argument("--successor-state-sha256", required=True)
    parser.add_argument("--boundary-evidence-json", default="{}")
    parser.add_argument("--authority-constraint", default="NONE")
    parser.add_argument("--hb-epoch", type=int, default=None)
    parser.add_argument("--current-state-json", default=None)
    args = parser.parse_args()
    source_path = args.transition_receipt or args.repo_receipt
    if not source_path:
        raise SystemExit("transition receipt required")
    receipt = load(source_path)
    default_class = ("REPO_STATE_PROPAGATION"
                     if receipt.get("schema") == "stegverse.repo-transition-receipt/v1"
                     else "ORGANIZATION_STATE_TRANSITION")
    try:
        result = append(
            receipt, args.org_transition_class or default_class,
            args.predecessor_state_sha256, args.successor_state_sha256,
            json.loads(args.boundary_evidence_json), args.authority_constraint,
            hb_epoch=args.hb_epoch,
            current_state=json.loads(args.current_state_json) if args.current_state_json else None)
    except LedgerLocationRequired as exc:
        print(json.dumps({
            "schema": "stegverse.organization-ledger-append-refusal/v1",
            "organization": C["organization"],
            "chain_verifies": False,
            "failed_predicate": exc.failed_predicate,
            "failure_code": "LEDGER_LOCATION_NOT_SUPPLIED",
            "required_evidence_or_repair": "supply the organization ledger root as " + exc.variable,
            "retry_entrypoint": "resident-runtime/aggregate_repo_transition.py::append",
            "next_attempt": "rerun once " + exc.variable + " is supplied; nothing was appended",
            "consequence_committed": False,
            "authority_constraint": "NONE_REFUSAL_ONLY",
            "time_constraint": "HEARTBEAT_EPOCH",
            "disposition_is_transition_evidence": False,
        }, sort_keys=True))
        raise SystemExit(1) from exc
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
