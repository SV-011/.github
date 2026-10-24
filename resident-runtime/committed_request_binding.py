#!/usr/bin/env python3
"""``COMMITTED_REQUEST_BINDING`` — a companion receipt binding a committed request to the receipts it produced.

The intr-destination self-closure (resident-runtime/intr_destination_ingress.py,
InTr code, unchanged here) writes the repository receipt and the organization
receipt. This module writes, in the same writer run and onto the same ledger
ref, one ``stegverse.committed-request-binding-receipt/v1`` that records the
committed request {path, sha256, hb_epoch_source: COMMITTED_REQUEST} and the
sha256 and ledger parent (previous_receipt_sha256) of each receipt it binds.

Binding receipts form their own chain under ``committed-request-bindings/`` in
the organization ledger root (its own HEAD). They are not appended to the
organization receipt chain: organization_ledger_readback.py requires every
receipt on that chain to be an organization transition receipt, and
merge_transfer.py reads its epoch from that chain's head.

Verification reads the ledger alone: each binding receipt verifies against its
body, and each bound receipt is read from its ledger, verifies against its body,
and has the recorded parent. Refusals are six fields and terminal; nothing is
written:

  COMMITTED_REQUEST_INVALID: the committed request pair is half-set or malformed.
  BOUND_RECEIPT_UNREADABLE: a bound receipt is missing, unreadable, does not
    verify against its body, or does not have the recorded parent.
  COMMITTED_REQUEST_DIGEST_UNVERIFIED (bind, verify): the committed request's
    sha256 is not the sha256 of the file actually committed at that path in the
    pushed revision (STEGVERSE_COMMITTED_REQUEST_SOURCE=<repository>#<revision>),
    or that file cannot be read there. A forged or rehashed committed_request is
    never bound and never verified; nothing is written.
  COMMITTED_REQUEST_REPLAY_MISMATCH: these receipts (a replayed packet) are
    already bound to a committed request with a different path or digest.
  COMMITTED_REQUEST_BINDING_ABSENT (verify): the closure's receipts have no
    binding, or are bound to a different committed request; never a silent ALLOW.
  COMMITTED_REQUEST_BINDING_CHAIN_INVALID (verify): a binding on the chain does
    not verify against its body, is not linked to its predecessor, or was not
    committed in the same ledger commit as the receipts it binds.

  UNDETERMINED_RETRY_IS_IDEMPOTENT (bind, seal; NOT terminal): writing the
    binding, its HEAD or the sealed commit faulted. The binding digest is a
    pure function of its inputs and the chain HEAD, and the receipt is put
    before the HEAD, so re-running completes the binding exactly once.

Binding receipts and the bindings HEAD on a directory store are written
atomically (staged beside the target, fsynced, os.replace), so a fault leaves
the old document or the new one, never a torn one; a git ref store commits
each put. A torn or partial read anywhere on the chain is
COMMITTED_REQUEST_BINDING_CHAIN_INVALID, never a traceback.

Each binding also records the organization chain HEAD at the moment of append
(organization_head_sha256), cross-linking the two chains. ``seal`` folds the
run's unpublished ledger commits into one commit on the published tip, so the
receipts and their binding are published in the same commit and the same
fast-forward push.

A replay of the same request against the same receipts returns the recorded
binding.

No clock is read. Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "stegverse.committed-request-binding-receipt/v1"
HEAD_SCHEMA = "stegverse.committed-request-binding-head/v1"
PREFIX = "committed-request-bindings/"
HEAD = PREFIX + "HEAD.json"
SOURCE = "COMMITTED_REQUEST"
GOAL = "SVORG-LLM-ORG-FOUNDATION-001"
ENTRYPOINT = ".github/workflows/organization-ledger-transition-writer.yml (push to main of " \
             ".stegverse/transition-ledger/requests/**)"
REQUEST_INVALID = "COMMITTED_REQUEST_INVALID"
DIGEST_UNVERIFIED = "COMMITTED_REQUEST_DIGEST_UNVERIFIED"
SOURCE_VARIABLE = "STEGVERSE_COMMITTED_REQUEST_SOURCE"
REQUEST_DIR = ".stegverse/transition-ledger/requests/"
RECEIPT_UNREADABLE = "BOUND_RECEIPT_UNREADABLE"
REPLAY_MISMATCH = "COMMITTED_REQUEST_REPLAY_MISMATCH"
BINDING_ABSENT = "COMMITTED_REQUEST_BINDING_ABSENT"
CHAIN_INVALID = "COMMITTED_REQUEST_BINDING_CHAIN_INVALID"
WRITE_FAULT = "UNDETERMINED_RETRY_IS_IDEMPOTENT"
#: OSError, LedgerWriteFault (a RuntimeError), ledger_receipt_collision (a ValueError)
WRITE_FAULTS = (OSError, RuntimeError, ValueError)
ORG_HEAD = "HEAD.json"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
LEVELS = (("repository", "STEGVERSE_REPO_LEDGER_ROOT", "repository_receipt_sha256"),
          ("organization", "STEGVERSE_ORG_LEDGER_ROOT", "organization_receipt_sha256"))

_spec = importlib.util.spec_from_file_location("ledger_store_for_binding", ROOT / "resident-runtime/ledger_store.py")
ledger_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger_store)


class Refused(Exception):
    def __init__(self, failure_code: str, failed_predicate: str, detail: str) -> None:
        super().__init__(detail)
        self.failure_code, self.failed_predicate, self.detail = failure_code, failed_predicate, detail


def refusal(exc: Refused) -> dict[str, Any]:
    repair = {
        REQUEST_INVALID: "a committed request path (printable token) and sha256 (sha256:<64 hex>)",
        DIGEST_UNVERIFIED: "the committed request path and sha256 of the file committed at that path in the "
                           "pushed revision",
        RECEIPT_UNREADABLE: "every bound receipt present on its ledger, verifying against its body, "
                            "with the recorded parent",
        REPLAY_MISMATCH: "a new packet_id for a different committed request; the recorded binding is unchanged",
        BINDING_ABSENT: "a binding receipt for exactly this committed request and these receipts, "
                        "committed with them",
        CHAIN_INVALID: "every binding verifies against its body, links to its predecessor and is committed "
                       "in the same ledger commit as the receipts it binds",
        WRITE_FAULT: "none: re-run the same writer; the binding completes exactly once",
    }[exc.failure_code]
    if exc.failure_code == WRITE_FAULT:
        return {
            "disposition": "FAIL_CLOSED",
            "failure_code": WRITE_FAULT,
            "failed_predicate": exc.failed_predicate,
            "required_evidence_or_repair": repair,
            "retry_entrypoint": ENTRYPOINT + " (re-run the same push)",
            "owning_existing_goal": GOAL,
            "next_attempt": "re-run; an already written binding is answered from the chain, never duplicated",
            "terminal": False,
            "detail": exc.detail,
            "production_receipt_claimed": False,
        }
    return {
        "disposition": "FAIL_CLOSED",
        "failure_code": exc.failure_code,
        "failed_predicate": exc.failed_predicate,
        "required_evidence_or_repair": repair,
        "retry_entrypoint": ENTRYPOINT,
        "owning_existing_goal": GOAL,
        "next_attempt": "none for this run: nothing was written; commit a corrected request or repair the ledger",
        "terminal": True,
        "detail": exc.detail,
        "production_receipt_claimed": False,
    }


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canon(value)).hexdigest()


def sealed(receipt: Any, digest: str) -> bool:
    if not isinstance(receipt, Mapping) or receipt.get("receipt_sha256") != digest:
        return False
    try:
        return sha({k: v for k, v in receipt.items() if k != "receipt_sha256"}) == digest
    except ValueError:
        return False


def committed_request(path: Any, digest: Any) -> dict[str, Any]:
    if not (isinstance(path, str) and path.strip() and len(path) <= 240
            and all(32 <= ord(character) <= 126 for character in path)):
        raise Refused(REQUEST_INVALID, "COMMITTED_REQUEST_PATH_IS_A_PRINTABLE_TOKEN", repr(path))
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise Refused(REQUEST_INVALID, "COMMITTED_REQUEST_SHA256_IS_A_DIGEST", repr(digest))
    return {"path": path, "sha256": digest, "hb_epoch_source": SOURCE}


def verified_request(binding: Mapping[str, Any]) -> None:
    """The binding's sha256 is the sha256 of the request file committed at its path in the pushed revision."""
    source = os.environ.get(SOURCE_VARIABLE, "")
    repository, separator, revision = source.rpartition("#")
    if not separator or not repository or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise Refused(DIGEST_UNVERIFIED, "COMMITTED_REQUEST_SOURCE_SUPPLIED", SOURCE_VARIABLE + "=" + repr(source))
    path = binding["path"]
    if not path.startswith(REQUEST_DIR) or not path.endswith(".json") or ".." in path.split("/"):
        raise Refused(DIGEST_UNVERIFIED, "COMMITTED_REQUEST_IS_UNDER_THE_REQUEST_DIRECTORY", path)
    try:
        committed = subprocess.run(["git", "-C", repository, "show", revision + ":" + path],
                                   check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Refused(DIGEST_UNVERIFIED, "COMMITTED_REQUEST_IS_READABLE_AT_THE_REVISION",
                      path + "@" + revision + ": " + str(exc)) from None
    actual = "sha256:" + hashlib.sha256(committed).hexdigest()
    if actual != binding["sha256"]:
        raise Refused(DIGEST_UNVERIFIED, "COMMITTED_REQUEST_DIGEST_IS_THE_COMMITTED_FILE",
                      path + "@" + revision + " is " + actual + ", not " + binding["sha256"])


def stores() -> dict[str, Any]:
    opened = {}
    for level, variable, _ in LEVELS:
        configured = os.environ.get(variable)
        if not configured:
            raise Refused(RECEIPT_UNREADABLE, "BOUND_LEDGER_LOCATION_SUPPLIED", variable + " is not set")
        try:
            opened[level] = ledger_store.open_store(ledger_store.parse_locator(configured))
        except ValueError as exc:
            raise Refused(RECEIPT_UNREADABLE, "BOUND_LEDGER_LOCATION_IS_VALID", str(exc)) from None
    return opened


def _get(store: Any, key: str) -> Any:
    try:
        return store.get(key)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        return exc


def _put(store: Any, key: str, document: Any) -> None:
    """Write one document so a fault leaves the old or the new, never a torn one."""
    encoded = ledger_store._document_bytes(json.loads(canon(document)))
    if not isinstance(store, ledger_store.Store):
        store.put(key, document)  # a git ref store commits each put
        return
    path = Path(store.locator(key))
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name("." + path.name + "." + str(os.getpid()) + ".partial")
    try:
        with open(staged, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if staged.exists():
            staged.unlink()


def bound_receipt(store: Any, level: str, digest: Any) -> dict[str, Any]:
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise Refused(RECEIPT_UNREADABLE, "BOUND_RECEIPT_DIGEST_SUPPLIED", level + " " + repr(digest))
    receipt = _get(store, ledger_store.receipt_key(digest))
    if not sealed(receipt, digest):
        raise Refused(RECEIPT_UNREADABLE, "BOUND_RECEIPT_VERIFIES_AGAINST_ITS_BODY", level + " " + digest)
    return {"level": level, "receipt_sha256": digest,
            "previous_receipt_sha256": receipt.get("previous_receipt_sha256")}


def bind(request: Mapping[str, Any], ingress_result: Mapping[str, Any], hb_epoch: int) -> dict[str, Any]:
    """Append the binding receipt; a replay of the same binding returns the recorded one."""
    try:
        binding = committed_request(request.get("path"), request.get("sha256"))
        verified_request(binding)
        if type(hb_epoch) is not int or hb_epoch < 32:
            raise Refused(REQUEST_INVALID, "HB_EPOCH_IS_A_SUPPLIED_INT_AT_LEAST_32", repr(hb_epoch))
        opened = stores()
        bound = [bound_receipt(opened[level], level, ingress_result.get(field)) for level, _, field in LEVELS]
    except Refused as exc:
        return refusal(exc)
    target = opened["organization"]
    try:
        return _bind_locked(target, binding, bound, hb_epoch)
    except Refused as exc:
        return refusal(exc)
    except WRITE_FAULTS as exc:
        return refusal(Refused(WRITE_FAULT, "BINDING_LEDGER_IS_WRITABLE", type(exc).__name__ + ": " + str(exc)))


def _bind_locked(target: Any, binding: dict[str, Any], bound: list[dict[str, Any]], hb_epoch: int) -> dict[str, Any]:
    target.initialize()
    with target.exclusive():
        for digest, receipt in chain(target):
            if receipt.get("bound_receipts") != bound:
                continue
            if receipt.get("committed_request") == binding:
                return {"disposition": "ALLOW", "binding_receipt_sha256": digest, "replayed": True,
                        "binding_receipt": receipt}
            return refusal(Refused(REPLAY_MISMATCH, "ONE_PACKET_BINDS_ONE_COMMITTED_REQUEST",
                                   "receipts already bound by " + digest + " to "
                                   + json.dumps(receipt.get("committed_request"), sort_keys=True)))
        head = _get(target, HEAD)
        org_head = _get(target, ORG_HEAD)
        if isinstance(org_head, BaseException):
            raise Refused(RECEIPT_UNREADABLE, "ORGANIZATION_HEAD_IS_READABLE", str(org_head))
        body = {
            "schema": SCHEMA,
            "committed_request": binding,
            "bound_receipts": bound,
            "hb_epoch": hb_epoch,
            "hb_epoch_source": SOURCE,
            "authority_constraint": "NONE_BINDING_ONLY",
            "time_constraint": "HEARTBEAT_EPOCH",
            "disposition_is_transition_evidence": True,
            "production_receipt_claimed": False,
            "organization_head_sha256": org_head.get("receipt_sha256") if isinstance(org_head, Mapping) else None,
            "previous_binding_sha256": head.get("receipt_sha256") if isinstance(head, Mapping) else None,
        }
        digest = sha(body)
        receipt = {**body, "receipt_sha256": digest}
        key = PREFIX + digest[7:] + ".json"
        existing = _get(target, key)
        if existing is not None and existing != receipt and not isinstance(existing, BaseException):
            return refusal(Refused(CHAIN_INVALID, "BINDING_KEY_HOLDS_ONLY_ITS_RECEIPT", key))
        try:
            # receipt before HEAD: a fault between them leaves an unreferenced receipt
            # that a re-run, computing the same digest, re-puts and then references
            _put(target, key, receipt)
            _put(target, HEAD, {"schema": HEAD_SCHEMA, "receipt_sha256": digest})
        except WRITE_FAULTS as exc:
            return refusal(Refused(WRITE_FAULT, "BINDING_RECEIPT_AND_HEAD_WRITTEN", type(exc).__name__ + ": " + str(exc)))
    return {"disposition": "ALLOW", "binding_receipt_sha256": digest, "replayed": False, "binding_receipt": receipt}


def chain(store: Any):
    """(digest, receipt) from the bindings HEAD back; a torn or partial read is CHAIN_INVALID."""
    head = _get(store, HEAD)
    if head is None:
        return
    if not isinstance(head, Mapping) or not (head.get("receipt_sha256") is None
                                              or isinstance(head.get("receipt_sha256"), str)):
        raise Refused(CHAIN_INVALID, "BINDINGS_HEAD_IS_READABLE", HEAD + ": " + repr(head)[:200])
    cursor = head.get("receipt_sha256")
    seen = set()
    while isinstance(cursor, str):
        if cursor in seen or not DIGEST.fullmatch(cursor):
            raise Refused(CHAIN_INVALID, "BINDING_CHAIN_IS_ACYCLIC_AND_ADDRESSED", cursor)
        seen.add(cursor)
        receipt = _get(store, PREFIX + cursor[7:] + ".json")
        if not isinstance(receipt, Mapping):
            raise Refused(CHAIN_INVALID, "BINDING_RECEIPT_IS_READABLE", cursor + ": " + repr(receipt)[:200])
        yield cursor, receipt
        cursor = receipt.get("previous_binding_sha256")


def _org_chain(store: Any, start: Any) -> list[str]:
    """Organization receipt digests from ``start`` back through previous_receipt_sha256, each sealed."""
    digests, cursor = [], start
    while isinstance(cursor, str) and cursor not in digests:
        receipt = _get(store, ledger_store.receipt_key(cursor))
        if not sealed(receipt, cursor):
            raise Refused(RECEIPT_UNREADABLE, "ORGANIZATION_CHAIN_VERIFIES_FROM_THE_RECORDED_HEAD", cursor)
        digests.append(cursor)
        cursor = receipt.get("previous_receipt_sha256")
    return digests


def _added_in(store: Any, key: str) -> str | None:
    """The ledger commit that added ``key`` (git ref stores); None for a directory store."""
    if not hasattr(store, "_git"):
        return None
    path = store._path(key)
    try:
        found = store._git("log", "--format=%H", "--diff-filter=A", "-1", store.ref, "--", path).stdout.decode().strip()
    except (OSError, RuntimeError) as exc:
        raise Refused(CHAIN_INVALID, "LEDGER_HISTORY_IS_READABLE", str(exc)) from None
    if not found:
        raise Refused(CHAIN_INVALID, "BOUND_KEY_IS_IN_LEDGER_HISTORY", path)
    return found


def verify(request: Mapping[str, Any], ingress_result: Mapping[str, Any]) -> dict[str, Any]:
    """ALLOW only when, from the ledger alone, these receipts carry this committed request's binding.

    The whole binding chain is checked (sealed, schema, hash-linked); the
    binding's bound receipts verify with their recorded parents; its recorded
    organization HEAD is on the organization chain and reaches the bound
    organization receipt; and on a git ref the binding was added in the same
    commit as both receipts.
    """
    try:
        binding = committed_request(request.get("path"), request.get("sha256"))
        verified_request(binding)
        opened = stores()
        org = opened["organization"]
        expected = {level: ingress_result.get(field) for level, _, field in LEVELS}
        links = list(chain(org))
        found = None
        for index, (digest, receipt) in enumerate(links):
            if not sealed(receipt, digest) or receipt.get("schema") != SCHEMA:
                raise Refused(CHAIN_INVALID, "BINDING_RECEIPT_VERIFIES_AGAINST_ITS_BODY", digest)
            successor = links[index - 1][1] if index else None
            if successor is not None and successor.get("previous_binding_sha256") != digest:
                raise Refused(CHAIN_INVALID, "BINDING_CHAIN_IS_HASH_LINKED", digest)
            recorded = {entry.get("level"): entry for entry in receipt.get("bound_receipts") or []}
            if found is None and {lv: e.get("receipt_sha256") for lv, e in recorded.items()} == expected:
                found = (digest, receipt, recorded)
        if found is None:
            raise Refused(BINDING_ABSENT, "CLOSURE_RECEIPTS_CARRY_A_BINDING",
                          "no binding names " + json.dumps(expected, sort_keys=True))
        digest, receipt, recorded = found
        if receipt.get("committed_request") != binding:
            raise Refused(BINDING_ABSENT, "BINDING_NAMES_THIS_COMMITTED_REQUEST",
                          digest + " binds " + json.dumps(receipt.get("committed_request"), sort_keys=True))
        for level, entry in recorded.items():
            if bound_receipt(opened[level], level, entry.get("receipt_sha256")) != entry:
                raise Refused(RECEIPT_UNREADABLE, "BOUND_RECEIPT_HAS_THE_RECORDED_PARENT", level)
        if expected["organization"] not in _org_chain(org, receipt.get("organization_head_sha256")):
            raise Refused(BINDING_ABSENT, "RECORDED_ORGANIZATION_HEAD_REACHES_THE_BOUND_RECEIPT",
                          str(receipt.get("organization_head_sha256")))
        commits = {_added_in(org, PREFIX + digest[7:] + ".json"),
                   _added_in(org, ledger_store.receipt_key(expected["organization"])),
                   _added_in(opened["repository"], ledger_store.receipt_key(expected["repository"]))}
        if len(commits) != 1:
            raise Refused(CHAIN_INVALID, "BINDING_IS_COMMITTED_WITH_THE_RECEIPTS_IT_BINDS", ",".join(
                sorted(str(commit) for commit in commits)))
        return {"disposition": "ALLOW", "verified": True, "binding_receipt_sha256": digest,
                "bindings_head_sha256": links[0][0], "bindings_chain_length": len(links),
                "organization_head_sha256": receipt.get("organization_head_sha256"),
                "ledger_commit": commits.pop(), "committed_request": binding,
                "bound_receipts": list(recorded.values()), "production_receipt_claimed": False}
    except Refused as exc:
        return refusal(exc)


def seal(published_tip: str | None, message: str) -> dict[str, Any]:
    """Fold this run's unpublished commits on the organization ledger ref into one commit on ``published_tip``."""
    try:
        return _seal(published_tip, message)
    except WRITE_FAULTS as exc:
        return refusal(Refused(WRITE_FAULT, "LEDGER_COMMITS_SEALED_INTO_ONE", type(exc).__name__ + ": " + str(exc)))


def _seal(published_tip: str | None, message: str) -> dict[str, Any]:
    store = ledger_store.open_store(ledger_store.parse_locator(os.environ.get("STEGVERSE_ORG_LEDGER_ROOT", "")))
    if not hasattr(store, "_git"):
        return refusal(Refused(CHAIN_INVALID, "SEAL_NEEDS_A_GIT_LEDGER_REF", str(store.locator(ORG_HEAD))))
    tip = store._tip()
    if tip is None or tip == published_tip:
        return refusal(Refused(CHAIN_INVALID, "RUN_APPENDED_TO_THE_REF", "tip " + str(tip)))
    if published_tip and store._git("merge-base", "--is-ancestor", published_tip, tip,
                                    check=False).returncode != 0:
        return refusal(Refused(CHAIN_INVALID, "PUBLISHED_TIP_IS_AN_ANCESTOR", published_tip))
    try:
        tree = store._git("rev-parse", tip + "^{tree}").stdout.decode().strip()
        args = ["commit-tree", "--no-gpg-sign", tree, "-m", message] + (["-p", published_tip] if published_tip else [])
        sealed_commit = store._git(*args).stdout.decode().strip()
        # compare-and-swap on the unsealed tip: a fault leaves the ref unsealed, and a re-run seals it
        store._git("update-ref", store.ref, sealed_commit, tip)
    except WRITE_FAULTS as exc:
        return refusal(Refused(WRITE_FAULT, "LEDGER_COMMITS_SEALED_INTO_ONE", type(exc).__name__ + ": " + str(exc)))
    return {"disposition": "ALLOW", "sealed_commit": sealed_commit, "parent": published_tip or None,
            "folded_tip": tip, "production_receipt_claimed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("bind", "seal", "verify"))
    parser.add_argument("--published-tip", default="", help="seal: the ledger ref tip before this run")
    parser.add_argument("--request-result", type=Path, required=True, help="transition_request.py output")
    parser.add_argument("--ingress-result", type=Path, required=True, help="intr_destination_ingress.py output")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        request = json.loads(args.request_result.read_text(encoding="utf-8"))
        ingress_result = json.loads(args.ingress_result.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result = refusal(Refused(RECEIPT_UNREADABLE, "RUN_RESULTS_ARE_READABLE", str(exc)))
    else:
        pair = {"path": request.get("request_path"), "sha256": request.get("request_sha256")}
        if args.action == "bind":
            result = bind(pair, ingress_result, request.get("hb_epoch"))
        elif args.action == "seal":
            result = seal(args.published_tip or None,
                          "ledger: committed request " + str(pair["path"]) + " " + str(pair["sha256"]))
        else:
            result = verify(pair, ingress_result)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result.get("disposition") == "ALLOW" else 1


if __name__ == "__main__":
    sys.exit(main())
