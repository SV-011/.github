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


def repository_ledger_root():
    """The caller-supplied repository ledger locator the source receipt must be on."""
    configured = os.getenv("STEGVERSE_REPO_LEDGER_ROOT")
    if not configured:
        raise ValueError("ledger_location_required_from_caller: STEGVERSE_REPO_LEDGER_ROOT")
    return ledger_store.parse_locator(configured)


def _source_on_ledger(source_receipt: Mapping[str, Any], source_store: Any) -> None:
    """The source receipt is on the repository ledger, reachable from HEAD, byte-for-byte.

    Unconditional: it runs for every source receipt, whether or not it carries a
    committed_request (OFF_LEDGER_SOURCE_RECEIPT_WITHOUT_BINDING_ACCEPTED).
    """
    digest = source_receipt["receipt_sha256"]
    head = source_store.get(ledger_store.HEAD_KEY)
    for receipt in _chain(source_store, head if isinstance(head, Mapping) else None):
        if receipt.get("receipt_sha256") == digest:
            if receipt != dict(source_receipt) or sha({k: v for k, v in receipt.items()
                                                       if k != "receipt_sha256"}) != digest:
                break
            return
    raise ledger_store.CommittedRequestRefused(
        "SOURCE_RECEIPT_NOT_ON_LEDGER", "SOURCE_RECEIPT_IS_ON_THE_REPOSITORY_LEDGER_REACHABLE_FROM_HEAD",
        "source receipt " + str(digest) + " is not the receipt the repository ledger holds on its chain",
        "aggregate only a sealed repository receipt that the repository ledger chain reaches from HEAD",
        "resident-runtime/aggregate_repo_transition.py append")


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


def append(source_receipt: Mapping[str, Any], org_transition_class: str,
           predecessor_state: str, successor_state: str, boundary_evidence: Mapping[str, Any],
           authority_constraint: str, hb_epoch: int | None = None,
           store: ledger_store.Store | None = None, idempotent: bool = False,
           source_store: Any = None) -> dict[str, Any]:
    if not isinstance(source_receipt, Mapping) or not isinstance(source_receipt.get("receipt_sha256"), str):
        raise ValueError("source_receipt_required")
    # before any write: the source must be on the repository ledger first, unconditionally,
    # then no committed_request may ride in the evidence or the source (V-P11 rows 5 and 3)
    _source_on_ledger(source_receipt, source_store or ledger_store.open_store(repository_ledger_root()))
    ledger_store.refuse_injected_committed_request("resident-runtime/aggregate_repo_transition.py append",
                                                   boundary_evidence=boundary_evidence,
                                                   source_receipt=source_receipt)
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
        "hb_reference": kernel.hb_reference(epoch=hb_epoch) if hb_epoch is not None else kernel.hb_reference(),
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
    raise RuntimeError("ledger_append_lost_comparison")
