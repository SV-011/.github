#!/usr/bin/env python3
"""Append the organization receipt that consumes one repository receipt.

The root comes from ``STEGVERSE_ORG_LEDGER_ROOT``. A plain path is a
directory. ``git+<repository>#<ref>`` addresses a git ref. The organization
receipt preserves the repository receipt digest. It does not select admission,
routing, or execution. Authority and time stay constraint variables. The
disposition on the evidence is transition evidence.

A receipt written here does not claim to be an authentic production receipt.
Callers that redirect the root, including tests, are writing a supplied store.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
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


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(value: Any) -> str:
    raw = value if isinstance(value, (bytes, bytearray)) else canon(value)
    return "sha256:" + hashlib.sha256(bytes(raw)).hexdigest()


def ledger_root():
    """The caller-supplied organization ledger locator. A missing root fails closed."""
    configured = os.getenv("STEGVERSE_ORG_LEDGER_ROOT")
    if not configured:
        raise ValueError("ledger_location_required_from_caller: STEGVERSE_ORG_LEDGER_ROOT")
    return ledger_store.parse_locator(configured)


def _chain(target: ledger_store.Store, head: Mapping[str, Any] | None):
    cursor = (head or {}).get("receipt_sha256") if isinstance(head, Mapping) else None
    while cursor:
        receipt = target.get(ledger_store.receipt_key(cursor))
        if not isinstance(receipt, dict):
            return
        yield receipt
        cursor = receipt.get("previous_receipt_sha256")


def _recorded(target: ledger_store.Store, head: Mapping[str, Any] | None,
              repo_receipt_sha256: str) -> dict[str, Any] | None:
    for receipt in _chain(target, head):
        if receipt.get("repo_receipt_sha256") == repo_receipt_sha256:
            return receipt
    return None


#: An append's epoch is supplied by its caller and checked by the kernel's
#: shared rule (SUBMISSION_CARRIES_THE_SUPPLIED_HB_EPOCH); an absent or invalid
#: one is refused six-field FAIL_CLOSED before anything is written.
EPOCH_RETRY_ENTRYPOINT = "resident-runtime/aggregate_repo_transition.py::append (hb_epoch=)"
OrgLedgerEpochRefused = kernel.SuppliedEpochRefused


def epoch_refusal(hb_epoch: Any) -> dict[str, Any] | None:
    """The six-field FAIL_CLOSED refusal of an append's epoch, or None."""
    return kernel.writer_epoch_refusal(hb_epoch, retry_entrypoint=EPOCH_RETRY_ENTRYPOINT)


def require_supplied_epoch(hb_epoch: Any) -> dict[str, Any]:
    """The heartbeat reference of the supplied epoch, or OrgLedgerEpochRefused."""
    return kernel.supplied_hb_reference(hb_epoch, retry_entrypoint=EPOCH_RETRY_ENTRYPOINT)


SOURCE_RECEIPT_IS_CLOCK_PROVENANCE = "SOURCE_RECEIPT_IS_CLOCK_PROVENANCE"
SOURCE_RETRY_ENTRYPOINT = "resident-runtime/aggregate_repo_transition.py::append (source_receipt=)"


def source_refusal(source_receipt: Mapping[str, Any]) -> dict[str, Any] | None:
    """The six-field FAIL_CLOSED for a source repository receipt of clock provenance, or None.

    A source receipt is admissible when its hb_reference says derived_from_clock
    exactly false. A clock-derived or unmarked one is never rebound here.
    """
    reference = source_receipt.get("hb_reference")
    if isinstance(reference, Mapping) and reference.get("derived_from_clock") is False:
        return None
    return {"disposition": "FAIL_CLOSED", "failure_code": SOURCE_RECEIPT_IS_CLOCK_PROVENANCE,
            "failed_predicate": "SOURCE_REPOSITORY_RECEIPT_REFERENCES_A_SUPPLIED_OR_CARRIED_EPOCH",
            "detail": "source repository receipt " + str(source_receipt.get("receipt_sha256"))
                      + " has hb_reference " + repr(reference),
            "required_evidence_or_repair": ("a WU-B13 migration transition that rebinds the legacy repo receipt "
                                            "under a supplied epoch with provenance CARRIED:LEGACY_CLOCK_RECEIPT "
                                            "+ source sha; existing receipts are never mutated"),
            "retry_entrypoint": SOURCE_RETRY_ENTRYPOINT,
            "owning_existing_goal": kernel.HB_EPOCH_OWNING_EXISTING_GOAL,
            "next_attempt": "after the WU-B13 migration transition; nothing was written",
            "derived_from_clock": False, "consequence_committed": False}


def append(source_receipt: Mapping[str, Any], org_transition_class: str,
           predecessor_state: str, successor_state: str, boundary_evidence: Mapping[str, Any],
           authority_constraint: str, hb_epoch: int | None = None,
           store: ledger_store.Store | None = None, idempotent: bool = False,
           hb_provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(source_receipt, Mapping) or not isinstance(source_receipt.get("receipt_sha256"), str):
        raise ValueError("source_receipt_required")
    # An absent or invalid epoch is FAIL_CLOSED before the store opens; no clock is read.
    hb = require_supplied_epoch(hb_epoch)
    hb = kernel.with_epoch_provenance(hb, hb_provenance)
    target = store or ledger_store.open_store(ledger_root())
    target.initialize()
    expected_head = target.get(ledger_store.HEAD_KEY)
    head = expected_head if isinstance(expected_head, dict) else None
    previous = (head or {}).get("receipt_sha256")
    source_sha = source_receipt["receipt_sha256"]
    if idempotent:
        prior = _recorded(target, head, source_sha)
        if prior is not None:
            if prior.get("org_transition_class") != org_transition_class:
                raise ValueError("ledger_receipt_collision")
            return prior
    # A recorded binding is answered above; a new one never binds a clock-provenance source.
    refused = source_refusal(source_receipt)
    if refused is not None:
        raise kernel.SuppliedEpochRefused(refused)
    refused = kernel.carried_provenance_refusal(
        hb_provenance, hb, target, ledger_store.receipt_key, ledger_store.HEAD_KEY,
        retry_entrypoint=EPOCH_RETRY_ENTRYPOINT)
    if refused is not None:
        raise kernel.SuppliedEpochRefused(refused)
    body = {
        "schema": "stegverse.organization-transition-receipt/v1",
        "organization": C["organization"],
        "repository": C["repository"],
        "org_transition_class": org_transition_class,
        "repo_receipt_sha256": source_sha,
        "predecessor_state_sha256": predecessor_state,
        "successor_state_sha256": successor_state,
        "evidence": dict(boundary_evidence),
        "authority_constraint": authority_constraint,
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": True,
        "production_receipt_claimed": False,
        "hb_reference": hb,
        "previous_receipt_sha256": previous,
    }
    digest = sha(body)
    receipt = {**body, "receipt_sha256": digest}
    new_head = {
        "schema": "stegverse.ledger-head/v1",
        "organization": C["organization"],
        "receipt_sha256": digest,
    }
    if target.append_transaction(
            ledger_store.receipt_key(digest), receipt,
            head if isinstance(head, dict) else None, new_head):
        return receipt
    raise ledger_store.LedgerWriteFault("ledger_append_lost_comparison")
