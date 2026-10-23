#!/usr/bin/env python3
"""H29(a): repository-ledger intake binding for SV-011 member repositories.

The binding is declared in .stegverse/transition-ledger/org-contract.json
``member_repository_ledgers``. Its shape restates StegVerse-org/LLM-adapter#431
@a2237bc3 (``repository_ledger_location`` and the
``stegverse.endpoint-ledger-designation/v1`` designation, store_type GIT_REF).
#431 is not admitted, so none of its code is vendored or imported here.

This module does two read-only things:

* ``intake``: evaluate a presented repository receipt for a bound member
  (today only SV-011/entity) against the binding and the consumed receipt
  schema. It never appends. The append leg is FAIL_CLOSED
  REPOSITORY_LEDGER_INTAKE_NOT_PRESENT until an admitted append path exists
  (#162 R-2 MERGE_APPEND_PATH_NOT_ADMITTED) and the genesis is pinned
  (#162 R-1 ORG_LEDGER_GENESIS_NOT_PINNED).
* ``readback``: read the member's subtree of the designated organization
  ledger ref and walk it to a sealed genesis. An absent subtree is never a
  genesis.

Every non-ALLOW result carries failure_code, failed_predicate,
required_evidence_or_repair, retry_entrypoint, owning_existing_goal and
next_attempt. A disposition is evidence that a transition was evaluated.
Authority and time stay constraint variables; no clock or network is read.
Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
ORG_CONTRACT = ROOT / ".stegverse/transition-ledger/org-contract.json"
SCHEMA = "stegverse.repository-ledger-intake-result/v1"
PREDICATE = "SV011_ENTITY_BOUND_AS_REPOSITORY_LEDGER"
GOAL = "SV011-TT-CAPABILITY-MAP-OBSERVATION-001 (StegVerse-org/.github#167 lane B, SV-011/.github#68)"
SOURCE_SHAPE_GOAL = "StegVerse-org/LLM-adapter#431 with StegVerse-org/.github#162 (Lane B 91117956)"
ENTRYPOINT = "resident-runtime/repository_ledger_intake.py"
DESIGNATION_SCHEMA = "stegverse.endpoint-ledger-designation/v1"
DESIGNATION_KEYS = ("endpoint_id", "endpoint_kind", "expected_genesis_receipt_sha256", "genesis_class",
                    "head_signer_source", "provider_selection", "rollback_watermark_source", "schema", "stores")
GIT_REF_KINDS = ("ORG", "PERMANENT")
INDEPENDENT_KINDS = ("KV", "SKAP_VAULT")
GIT_STORE_KEYS = ("namespace", "provider", "ref", "repository", "store_type")
SIX = ("failure_code", "failed_predicate", "required_evidence_or_repair", "retry_entrypoint",
       "owning_existing_goal", "next_attempt")

_spec = importlib.util.spec_from_file_location("ledger_store_h29a", ROOT / "resident-runtime/ledger_store.py")
ledger_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger_store)


def six(disposition: str, failure_code: str, failed_predicate: str, required_evidence_or_repair: str,
        retry_entrypoint: str, owning_existing_goal: str, next_attempt: str, **extra: Any) -> dict[str, Any]:
    result = {"schema": SCHEMA, "disposition": disposition, "failure_code": failure_code,
              "failed_predicate": failed_predicate, "required_evidence_or_repair": required_evidence_or_repair,
              "retry_entrypoint": retry_entrypoint, "owning_existing_goal": owning_existing_goal,
              "next_attempt": next_attempt}
    if disposition not in ("DENY", "FAIL_CLOSED") or any(not str(result[k]).strip() for k in SIX):
        raise ValueError("NON_ALLOW_SIX_FIELDS_PRESENT")
    result.update(extra)
    return result


def append_leg(member: str) -> dict[str, Any]:
    """The append leg as it stands: no admitted path appends a member repository receipt."""
    return six("FAIL_CLOSED", "REPOSITORY_LEDGER_INTAKE_NOT_PRESENT", PREDICATE,
               "an admitted append path that carries a " + member + " repository receipt onto "
               "repository-ledger/" + member + " of SV-011/.github refs/stegverse/organization-ledger under "
               "ORGANIZATION_LEDGER_LOCK (the #162 R-2 MERGE_APPEND_PATH_NOT_ADMITTED repair, applied to SV-011), "
               "after the genesis pin (#162 R-1)", ENTRYPOINT + "::intake", GOAL,
               "after an append path for member repositories is admitted; this module never appends")


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def seal(body: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canon(body)).hexdigest()


def sealed(receipt: Mapping[str, Any]) -> bool:
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    return isinstance(receipt.get("receipt_sha256"), str) and receipt["receipt_sha256"] == seal(body)


def load_org_contract(path: Path = ORG_CONTRACT) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def binding_for(member: str, org: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The declared binding for ``member`` and None, or None and a six-field refusal."""
    declared = org.get("member_repository_ledgers")
    bindings = declared.get("bindings") if isinstance(declared, Mapping) else None
    found = [b for b in bindings or () if isinstance(b, Mapping) and b.get("member_repository") == member]
    if len(found) != 1:
        return None, six("FAIL_CLOSED", "REPOSITORY_LEDGER_BINDING_NOT_DECLARED", PREDICATE,
                         "exactly one member_repository_ledgers binding for " + member + " in org-contract.json",
                         ".stegverse/transition-ledger/org-contract.json member_repository_ledgers", GOAL,
                         "on an admitted change declaring the binding", member_repository=member)
    failure = binding_failure(found[0], org)
    return (None, failure) if failure else (dict(found[0]), None)


def _invalid(member: str, detail: str) -> dict[str, Any]:
    return six("FAIL_CLOSED", "REPOSITORY_LEDGER_BINDING_INVALID", "REPOSITORY_LEDGER_BINDING_WELL_FORMED",
               "a binding in the #431@a2237bc3 shape: repository_ledger_location on this organization's "
               "designated ref with namespace repository-ledger/<member> and new_ledger_root false, and a "
               "stegverse.endpoint-ledger-designation/v1 with one GIT_REF store equal to that location (" + detail + ")",
               ".stegverse/transition-ledger/org-contract.json member_repository_ledgers", GOAL,
               "on an admitted correction of the binding", member_repository=member)


def binding_failure(binding: Mapping[str, Any], org: Mapping[str, Any]) -> dict[str, Any] | None:
    member = str(binding.get("member_repository"))
    location = binding.get("repository_ledger_location")
    designation = binding.get("ledger_designation")
    if not isinstance(location, Mapping) or not isinstance(designation, Mapping):
        return _invalid(member, "location or designation absent")
    namespace = "repository-ledger/" + member
    expected = {"repository": org.get("organization_ledger_repository"), "ref": org.get("organization_ledger_ref"),
                "namespace": namespace, "new_ledger_root": False}
    wrong = sorted(k for k, v in expected.items() if location.get(k) != v)
    if wrong:
        return _invalid(member, "location fields " + ",".join(wrong))
    if tuple(sorted(designation)) != DESIGNATION_KEYS or designation.get("schema") != DESIGNATION_SCHEMA:
        return _invalid(member, "designation keys or schema")
    if designation.get("endpoint_kind") in INDEPENDENT_KINDS:
        return six("FAIL_CLOSED", "KV_LEDGER_DESIGNATED_ON_GIT_REF", "INDEPENDENT_ENDPOINT_LEDGER_NOT_ORG_HOSTED",
                   "KV and SKAP_VAULT keep their own recorded receipt chain in the KV (#167 comment 6372442705); "
                   "they are never bound on refs/stegverse/organization-ledger",
                   ".stegverse/transition-ledger/org-contract.json member_repository_ledgers", GOAL,
                   "on an admitted correction of the binding", member_repository=member)
    if designation.get("endpoint_kind") not in GIT_REF_KINDS:
        return _invalid(member, "endpoint_kind")
    if designation.get("endpoint_id") != member or designation.get("provider_selection") != "SINGLE_PROVIDER_V1":
        return _invalid(member, "endpoint_id or provider_selection")
    stores = designation.get("stores")
    if not isinstance(stores, list) or len(stores) != 1:
        return six("FAIL_CLOSED", "ENDPOINT_LEDGER_MULTI_PROVIDER_NOT_SUPPORTED",
                   "ONE_STORE_PROVIDER_UNDER_SINGLE_PROVIDER_V1", "designate exactly one store",
                   ".stegverse/transition-ledger/org-contract.json member_repository_ledgers", GOAL,
                   "on an admitted correction of the binding", member_repository=member)
    store = stores[0]
    if (not isinstance(store, Mapping) or tuple(sorted(store)) != GIT_STORE_KEYS or store.get("store_type") != "GIT_REF"
            or store.get("provider") != "git" or any(store.get(k) != location.get(k) for k in ("repository", "ref", "namespace"))):
        return _invalid(member, "store is not the GIT_REF location")
    if designation.get("head_signer_source") != "ORG_LEDGER_HEAD_SIGNER":
        return _invalid(member, "head_signer_source")
    if designation.get("expected_genesis_receipt_sha256") != location.get("expected_genesis_receipt_sha256"):
        return _invalid(member, "genesis pins disagree")
    return None


def genesis_not_pinned(member: str) -> dict[str, Any]:
    return six("FAIL_CLOSED", "ORG_LEDGER_GENESIS_NOT_PINNED", "ENDPOINT_LEDGER_GENESIS_PINNED_BY_DESIGNATION",
               "a REPOSITORY_LEDGER_INITIALIZATION genesis for repository-ledger/" + member + " appended by the "
               "organization's own append and read back, then expected_genesis_receipt_sha256 set from it by an "
               "admitted change (#162 R-1, applied to SV-011)",
               ".stegverse/transition-ledger/org-contract.json member_repository_ledgers; then " + ENTRYPOINT,
               GOAL + "; source shape " + SOURCE_SHAPE_GOAL,
               "after the genesis append is admitted and read back", member_repository=member)


def intake(member: str, receipt: Any, org: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate a presented repository receipt for ``member``. Never appends; never ALLOW in this build."""
    org = load_org_contract() if org is None else org
    binding, failure = binding_for(member, org)
    if failure:
        return {**failure, "append_leg": append_leg(member)}
    consumed = org["member_repository_ledgers"].get("consumed_receipt_schema")
    if receipt is None:
        result = six("FAIL_CLOSED", "SV011_ENTITY_REPOSITORY_RECEIPT_ABSENT", PREDICATE,
                     "a sealed " + str(consumed) + " receipt emitted by " + member + " for its own transition",
                     ENTRYPOINT + "::intake", GOAL,
                     "when " + member + " emits a repository receipt in the consumed schema",
                     member_repository=member)
    elif not isinstance(receipt, Mapping) or receipt.get("schema") != consumed:
        got = receipt.get("schema") if isinstance(receipt, Mapping) else type(receipt).__name__
        result = six("FAIL_CLOSED", "REPOSITORY_RECEIPT_SCHEMA_NOT_CONSUMED", PREDICATE,
                     member + " emits " + str(got) + "; the organization consumes " + str(consumed) +
                     " (org-contract.json consumes). Repair: " + member + " emits the consumed schema by its own "
                     "admitted change", ENTRYPOINT + "::intake", GOAL,
                     "after " + member + " emits " + str(consumed), member_repository=member, presented_schema=got)
    elif receipt.get("repository") != member:
        result = six("DENY", "REPOSITORY_RECEIPT_NOT_FOR_BOUND_REPOSITORY", PREDICATE,
                     "a receipt whose repository is " + member, ENTRYPOINT + "::intake", GOAL,
                     "on a receipt for the bound repository", member_repository=member)
    elif not sealed(receipt):
        result = six("FAIL_CLOSED", "REPOSITORY_RECEIPT_UNSEALED", PREDICATE,
                     "receipt_sha256 equal to the sha256 of the canonical receipt body", ENTRYPOINT + "::intake",
                     GOAL, "on a sealed receipt", member_repository=member)
    elif binding["repository_ledger_location"].get("expected_genesis_receipt_sha256") is None:
        result = genesis_not_pinned(member)
    else:
        result = append_leg(member)
        result["member_repository"] = member
    result["append_leg"] = append_leg(member)
    return result


def readback(member: str, location: str, org: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Read repository-ledger/<member> from ``location`` (git+<repo>#<ref>) and walk it to genesis."""
    org = load_org_contract() if org is None else org
    binding, failure = binding_for(member, org)
    if failure:
        return failure
    loc = binding["repository_ledger_location"]
    parsed = ledger_store.parse_locator(location)
    if not isinstance(parsed, ledger_store.GitLocator) or parsed.ref != loc["ref"] or parsed.namespace:
        return six("FAIL_CLOSED", "READBACK_LOCATION_NOT_THE_DESIGNATED_REF", "READBACK_READS_THE_DESIGNATED_REF",
                   "a git+<repository>#" + loc["ref"] + " location with no namespace (the binding supplies it)",
                   ENTRYPOINT + " readback", GOAL, "on a corrected location", member_repository=member)
    store = ledger_store.GitLedgerStore(parsed.repository, parsed.ref, loc["namespace"])
    head = store.get(ledger_store.HEAD_KEY)
    if not isinstance(head, Mapping) or not isinstance(head.get("receipt_sha256"), str):
        return six("FAIL_CLOSED", "ENDPOINT_LEDGER_EMPTY_NOT_A_GENESIS", "ENDPOINT_LEDGER_HAS_A_SEALED_GENESIS",
                   "a genesis receipt under " + loc["namespace"] + "; an absent subtree is never taken as a genesis",
                   ENTRYPOINT + " readback", GOAL, "after the genesis append (#162 R-1, R-2 applied to SV-011)",
                   member_repository=member, namespace=loc["namespace"], append_leg=append_leg(member))
    chain, cursor, seen = [], head["receipt_sha256"], set()
    while cursor:
        receipt = store.get(ledger_store.receipt_key(cursor)) if cursor not in seen else None
        seen.add(cursor)
        if not isinstance(receipt, Mapping) or receipt.get("receipt_sha256") != cursor or not sealed(receipt) \
                or receipt.get("repository") != member:
            return six("FAIL_CLOSED", "ENDPOINT_LEDGER_UNREADABLE", "ENDPOINT_LEDGER_CHAIN_SEALED_AND_LINKED",
                       "every receipt under " + loc["namespace"] + " sealed, for " + member + ", and linked to genesis",
                       ENTRYPOINT + " readback", GOAL, "on the next read-back", member_repository=member,
                       at_receipt=cursor)
        chain.append(receipt)
        cursor = receipt.get("previous_receipt_sha256")
    genesis = chain[-1]["receipt_sha256"]
    pin = loc.get("expected_genesis_receipt_sha256")
    if pin is None:
        return {**genesis_not_pinned(member), "chain_length": len(chain), "observed_genesis_receipt_sha256": genesis}
    if pin != genesis:
        return six("DENY", "ENDPOINT_LEDGER_GENESIS_MISMATCH", "ENDPOINT_LEDGER_GENESIS_PINNED_BY_DESIGNATION",
                   "the subtree's genesis equal to the binding's pin", ENTRYPOINT + " readback", GOAL,
                   "on the next read-back", member_repository=member, observed_genesis_receipt_sha256=genesis)
    return {"schema": SCHEMA, "disposition": "ALLOW", "member_repository": member, "namespace": loc["namespace"],
            "chain_length": len(chain), "head_receipt_sha256": head["receipt_sha256"],
            "genesis_receipt_sha256": genesis, "append_leg": append_leg(member)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("intake")
    one.add_argument("--member", required=True)
    one.add_argument("--receipt", help="path to a presented repository receipt (absent: no receipt)")
    two = sub.add_parser("readback")
    two.add_argument("--member", required=True)
    two.add_argument("--location", required=True)
    args = parser.parse_args(argv)
    if args.command == "intake":
        receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8")) if args.receipt else None
        result = intake(args.member, receipt)
    else:
        result = readback(args.member, args.location)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("disposition") == "ALLOW" else 1


if __name__ == "__main__":
    sys.exit(main())
