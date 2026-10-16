#!/usr/bin/env python3
"""Append one repository transition receipt.

The root comes from ``STEGVERSE_REPO_LEDGER_ROOT``. A missing root fails
closed: this module does not invent a ledger location. The receipt is
transition evidence. ``authority_constraint`` records the authority constraint
variable. The heartbeat epoch, when supplied, records the time constraint.
Neither selects admission, routing, or execution.

A chain reachable from HEAD is verified before an append. A missing or
corrupted node fails closed and writes nothing. The substrate transaction
publishes the receipt and HEAD together.

A receipt written here is not an authentic production receipt. Callers that
redirect the root, including tests, are writing a supplied store.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
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
CHAIN_BREAK = "REPO_LEDGER_CHAIN_BREAK"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(value: Any) -> str:
    raw = value if isinstance(value, (bytes, bytearray)) else canon(value)
    return "sha256:" + hashlib.sha256(bytes(raw)).hexdigest()


def lr():
    configured = os.getenv("STEGVERSE_REPO_LEDGER_ROOT")
    if not configured:
        raise ValueError("ledger_location_required_from_caller: STEGVERSE_REPO_LEDGER_ROOT")
    return ledger_store.parse_locator(configured)


class RepoLedgerChainBreak(ValueError):
    """A node reachable from HEAD is missing, malformed, or not the receipt its digest names."""

    failed_predicate = CHAIN_BREAK

    def __init__(self, receipt_sha256: str | None, detail: str) -> None:
        super().__init__(CHAIN_BREAK + ": " + detail + ": " + repr(receipt_sha256))
        self.receipt_sha256 = receipt_sha256
        self.detail = detail


def chain_break_refusal(exc: RepoLedgerChainBreak) -> dict[str, Any]:
    """The append attempt's observation when the chain it would extend is broken."""
    return {
        "schema": "stegverse.repo-ledger-append-refusal/v1",
        "repository": C["repository"],
        "chain_verifies": False,
        "failed_predicate": exc.failed_predicate,
        "detail": exc.detail,
        "receipt_sha256": exc.receipt_sha256,
        "failure_code": "REPO_LEDGER_CHAIN_BROKEN",
        "required_evidence_or_repair": "restore the receipt addressed by receipt_sha256, byte-for-byte as minted; a chain is never re-minted over a break",
        "retry_entrypoint": ".stegverse/transition-ledger/emit.py::append",
        "next_attempt": "rerun the same append once the addressed receipt is restored; nothing was appended",
        "consequence_committed": False,
        "authority_constraint": "NONE_REFUSAL_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": False,
    }


def node(target: ledger_store.Store, cursor: str | None) -> dict[str, Any]:
    """The receipt ``cursor`` addresses, recomputed and proven to be that receipt."""
    if not isinstance(cursor, str) or not DIGEST.match(cursor):
        raise RepoLedgerChainBreak(cursor if isinstance(cursor, str) else None, "malformed_receipt_address")
    try:
        receipt = target.get(ledger_store.receipt_key(cursor))
    except ValueError:
        raise RepoLedgerChainBreak(cursor, "unreadable_receipt") from None
    if receipt is None:
        raise RepoLedgerChainBreak(cursor, "missing_receipt")
    if not isinstance(receipt, dict):
        raise RepoLedgerChainBreak(cursor, "malformed_receipt")
    if sha({key: value for key, value in receipt.items() if key != "receipt_sha256"}) != cursor:
        raise RepoLedgerChainBreak(cursor, "receipt_body_does_not_hash_to_its_address")
    if receipt.get("receipt_sha256") != cursor:
        raise RepoLedgerChainBreak(cursor, "stored_receipt_sha256_is_not_its_address")
    return receipt


def chain(target: ledger_store.Store, head: Mapping[str, Any] | None):
    """Every receipt reachable from ``head``, newest first, each one verified."""
    if head is None:
        return
    cursor = head.get("receipt_sha256") if isinstance(head, Mapping) else None
    if cursor is None:
        raise RepoLedgerChainBreak(None, "malformed_head")
    while cursor is not None:
        receipt = node(target, cursor)
        yield receipt
        cursor = receipt.get("previous_receipt_sha256")


def recorded(target: ledger_store.Store, head: Mapping[str, Any] | None,
             transition_id: str, transition_class: str) -> dict[str, Any] | None:
    """The receipt this chain already holds for a transition.

    The whole chain is walked and verified even after a match, so an answer is
    never read off a chain that is broken further back.
    """
    found = None
    for receipt in chain(target, head):
        if (found is None and receipt.get("transition_id") == transition_id
                and receipt.get("transition_class") == transition_class):
            found = receipt
    return found


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
    mine = dict(evidence) if isinstance(evidence, Mapping) else {}
    for _attempt in range(128):
        expected_head = target.get(ledger_store.HEAD_KEY)
        head = expected_head if isinstance(expected_head, dict) else None
        previous = (head or {}).get("receipt_sha256")
        prior = recorded(target, head, transition_id, transition_class)
        if idempotent_on is not None and prior is not None:
            theirs = prior.get("evidence")
            theirs = theirs if isinstance(theirs, dict) else {}
            if (prior.get("predecessor_state_sha256") != predecessor_state_sha256
                    or prior.get("successor_state_sha256") != successor_state_sha256
                    or prior.get("authority_constraint") != authority_constraint
                    or any(theirs.get(key, _ABSENT) != mine.get(key, _ABSENT) for key in idempotent_on)):
                raise ValueError("ledger_receipt_collision")
            return prior
        heartbeat = kernel.hb_reference(epoch=hb_epoch) if hb_epoch is not None else kernel.hb_reference()
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
            "hb_reference": heartbeat,
            "previous_receipt_sha256": previous,
        }
        digest = sha(body)
        receipt = {**body, "receipt_sha256": digest}
        new_head = {
            "schema": "stegverse.ledger-head/v1",
            "repository": C["repository"],
            "receipt_sha256": digest,
            "receipt_path": target.locator(ledger_store.receipt_key(digest)),
        }
        if target.append_transaction(ledger_store.receipt_key(digest), receipt, expected_head, new_head):
            return receipt
    raise SystemExit("REPO_LEDGER_APPEND_CONTENTION_EXHAUSTED")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--transition-id", required=True)
    parser.add_argument("--transition-class", required=True)
    parser.add_argument("--predecessor-state-sha256", required=True)
    parser.add_argument("--successor-state-sha256", required=True)
    parser.add_argument("--evidence-json", default="{}")
    parser.add_argument("--authority-constraint", default="NONE")
    parser.add_argument("--hb-epoch", type=int, default=None)
    args = parser.parse_args()
    try:
        receipt = append(
            args.transition_id, args.transition_class, args.predecessor_state_sha256,
            args.successor_state_sha256, json.loads(args.evidence_json),
            args.authority_constraint, args.hb_epoch)
    except RepoLedgerChainBreak as exc:
        print(json.dumps(chain_break_refusal(exc), sort_keys=True))
        raise SystemExit(1) from exc
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
