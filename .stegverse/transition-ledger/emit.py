#!/usr/bin/env python3
"""Append one repository transition receipt.

The root comes from ``STEGVERSE_REPO_LEDGER_ROOT``. A missing root fails
closed: this module does not invent a ledger location. A plain path is a
directory. ``git+<repository>#<ref>`` addresses a git ref, and
``:<namespace>`` addresses a subtree of that ref. The receipt is transition
evidence. ``authority_constraint`` records the authority constraint variable.
The heartbeat epoch, when supplied, records the time constraint. Neither
selects admission, routing, or execution.

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

ROOT = Path(__file__).resolve().parents[2]
C = json.loads((ROOT / ".stegverse/transition-ledger/contract.json").read_text(encoding="utf-8"))

_kernel_spec = importlib.util.spec_from_file_location("kernel", ROOT / "org-kernel/kernel.py")
kernel = importlib.util.module_from_spec(_kernel_spec)
_kernel_spec.loader.exec_module(kernel)

_store_spec = importlib.util.spec_from_file_location(
    "ledger_store", ROOT / "resident-runtime/ledger_store.py")
ledger_store = importlib.util.module_from_spec(_store_spec)
_store_spec.loader.exec_module(ledger_store)

_ABSENT = object()


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(value: Any) -> str:
    raw = value if isinstance(value, (bytes, bytearray)) else canon(value)
    return "sha256:" + hashlib.sha256(bytes(raw)).hexdigest()


def lr() -> Path:
    configured = os.getenv("STEGVERSE_REPO_LEDGER_ROOT")
    if not configured:
        raise ValueError("ledger_location_required_from_caller: STEGVERSE_REPO_LEDGER_ROOT")
    return ledger_store.parse_locator(configured)


def _chain(target: ledger_store.Store, head: Mapping[str, Any] | None):
    cursor = (head or {}).get("receipt_sha256") if isinstance(head, Mapping) else None
    while cursor:
        receipt = target.get(ledger_store.receipt_key(cursor))
        if not isinstance(receipt, dict):
            return
        yield receipt
        cursor = receipt.get("previous_receipt_sha256")


def recorded(target: ledger_store.Store, head: Mapping[str, Any] | None,
             transition_id: str, transition_class: str) -> dict[str, Any] | None:
    for receipt in _chain(target, head):
        if (receipt.get("transition_id") == transition_id
                and receipt.get("transition_class") == transition_class):
            return receipt
    return None


def append(transition_id: str, transition_class: str, predecessor_state_sha256: str,
           successor_state_sha256: str, evidence: Mapping[str, Any] | None = None,
           authority_constraint: str = "NONE", hb_epoch: int | None = None,
           store: ledger_store.Store | None = None,
           idempotent_on: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Append one repository receipt, or return the one this transition already has.

    With ``idempotent_on``, a transition the chain already records is returned.
    The same transition id with a different predecessor, successor, authority
    constraint, or named evidence field is ``ledger_receipt_collision``. The
    heartbeat epoch is not compared: a retry may arrive on another tick, and
    the tick is a time constraint, not a second transition.
    """
    target = store or ledger_store.open_store(lr())
    target.initialize()
    expected_head = target.get(ledger_store.HEAD_KEY)
    previous_head = expected_head if isinstance(expected_head, dict) else None
    previous = (previous_head or {}).get("receipt_sha256")
    prior = recorded(target, previous_head, transition_id, transition_class)
    mine = dict(evidence) if isinstance(evidence, Mapping) else {}
    if idempotent_on is not None and prior is not None:
        theirs = prior.get("evidence")
        theirs = theirs if isinstance(theirs, dict) else {}
        if (prior.get("predecessor_state_sha256") != predecessor_state_sha256
                or prior.get("successor_state_sha256") != successor_state_sha256
                or prior.get("authority_constraint") != authority_constraint
                or any(theirs.get(key, _ABSENT) != mine.get(key, _ABSENT) for key in idempotent_on)):
            raise ValueError("ledger_receipt_collision")
        return prior
    body = {
        "schema": "stegverse.repo-transition-receipt/v1",
        "repository": C["repository"],
        "transition_id": transition_id,
        "transition_class": transition_class,
        "predecessor_state_sha256": predecessor_state_sha256,
        "successor_state_sha256": successor_state_sha256,
        "evidence": mine,
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
        "repository": C["repository"],
        "receipt_sha256": digest,
    }
    if target.append_transaction(ledger_store.receipt_key(digest), receipt, previous_head, new_head):
        return receipt
    raise RuntimeError("ledger_append_lost_comparison")
