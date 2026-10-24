#!/usr/bin/env python3
"""InTr mesh path ingress: the destination half of a path transfer.

Carrier (a manifest transition, no cross-organization token): the source's
sealed path receipts (operation INTR_MESH_PATH_TRANSFER, appended on the
source's own ledger by resident-runtime/intr_path_transfer.py) are carried, as
byte-for-value copies, in a row of this repository's
.stegverse/intr-path-transfer/inbound.json. Landing that row on this
repository's main is the destination transition. After the push's main-merge
transfer is ledgered, .github/workflows/intr-path-ingress.yml runs
``consume``: each row is checked (both copies sealed, the organization receipt
links the repository receipt, the evidence names this organization as the
destination at its derived address, the message is the one this
organization's intr_destination_ingress.py accepts), and the message enters
this organization's ingress operation: one repository receipt and one
organization receipt (operation ORGANIZATION_INTR_DESTINATION_INGRESS) are
appended through intr_destination_ingress.py::_append, at the ledger head's
supplied epoch, with boundary evidence linking the source receipts by seal.

What the destination cannot observe without a credential: that the carried
source receipts are included in the source organization's ledger. The source
repositories are internal; GITHUB_TOKEN is scoped to this repository. That
predicate is read through the TV/TVC issuance transition (StegVerse-Labs/TVC
TVC-PRIVATE-SOURCE-READ-001, InTr path consumer INTR-PATH-LEDGER-READBACK-001;
resident-runtime/intr_path_tvc_read.py): until a TVC read receipt for the scope
is landed at .stegverse/intr-path-transfer/tvc-read-receipts/ and verifies, it is
FAIL_CLOSED INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED with retry at that
TVC transition. ``verify_end_to_end`` turns ALLOW only from readbacks of both
ledgers: the source ledger's sealed path receipts and this ledger's sealed
ingress receipts linking them; ``end_to_end_via_tvc`` reads this ledger itself
and the other organization's from its verified TVC read receipt.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]


def _module(name: str, relative: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mt = _module("merge_transfer_for_path_ingress", "resident-runtime/merge_transfer.py")
ingress = mt.ingress
tvc_read = _module("intr_path_tvc_read", "resident-runtime/intr_path_tvc_read.py")
Refused = getattr(mt, "Refused", type("Refused", (Exception,), {"result": {}}))
TVC_FIXTURE_OK = False   # tests only: accept FIXTURE_NOT_AUTHENTIC TVC read receipts

SOURCE_OPERATION = "INTR_MESH_PATH_TRANSFER"
SOURCE_EVIDENCE_SCHEMA = "stegverse.intr-path-transfer-evidence/v1"
OPERATION = ingress.OPERATION_ID
TRANSITION_CLASS = "INTR_MESH_PATH_INGRESS"
EVIDENCE_SCHEMA = "stegverse.intr-path-ingress-evidence/v1"
INBOUND_SCHEMA = "stegverse.intr-path-inbound/v1"
RESULT_SCHEMA = "stegverse.intr-path-ingress-result/v1"
READBACK_SCHEMA = "stegverse.intr-path-ledger-readback/v1"
INBOUND = ROOT / ".stegverse" / "intr-path-transfer" / "inbound.json"
GOAL = "StegVerse-org/.github#162"
ENTRYPOINT = (".github/workflows/intr-path-ingress.yml (after Organization Ledger Main Merge Transfer "
              "completes on a push to main)")
CREDENTIAL_ENTRYPOINT = tvc_read.TVC_TRANSITION
CREDENTIAL_GOAL = tvc_read.TVC_GOAL
SIX_FIELDS = mt.SIX_FIELDS
ALLOW, DENY, FAIL_CLOSED = "ALLOW", "DENY", "FAIL_CLOSED"

NOT_CARRIED = "INTR_PATH_SOURCE_RECEIPT_NOT_CARRIED"
CARRIED_CONTRADICTED = "INTR_PATH_CARRIED_RECEIPT_CONTRADICTED"
NOT_LEDGERED = "INTR_PATH_DESTINATION_RECEIPT_NOT_LEDGERED"
LINK_UNVERIFIED = "INTR_PATH_DESTINATION_RECEIPT_LINK_UNVERIFIED"
CREDENTIAL_NOT_ISSUED = "INTR_PATH_CROSS_ORG_READ_CREDENTIAL_NOT_ISSUED"
SOURCE_NOT_LEDGERED = "INTR_PATH_RECEIPT_NOT_LEDGERED"
SOURCE_LINK_UNVERIFIED = "INTR_PATH_RECEIPT_LINK_UNVERIFIED"

sha = ingress.sha
sealed = mt.sealed


def repository() -> str:
    return mt.repository()


def organization() -> str:
    return repository().split("/")[0]


def address(org: str) -> str:
    """The kernel's derivation (org-kernel/kernel.py organization_slug) plus the destination profile."""
    kernel = _module("org_kernel_for_path_ingress", "org-kernel/kernel.py")
    return kernel.organization_slug(org) + "." + ingress.PROFILE_ID


def refusal(disposition: str, code: str, predicate: str, repair: str, next_attempt: str, *,
            entrypoint: str = ENTRYPOINT, goal: str = GOAL, **extra: Any) -> dict[str, Any]:
    out = {"schema": RESULT_SCHEMA, "disposition": disposition, "failure_code": code,
           "failed_predicate": predicate, "required_evidence_or_repair": repair,
           "retry_entrypoint": entrypoint, "owning_existing_goal": goal, "next_attempt": next_attempt}
    out.update(extra)
    return out


def _payload_ok(evidence: Mapping[str, Any]) -> bool:
    return evidence.get("payload_sha256") == sha({k: v for k, v in evidence.items()
                                                  if k not in ("payload_sha256", "disposition", "communication_id")})


# -- the carried source receipts ------------------------------------------------

def check_source_pair(org_receipt: Any, repo_receipt: Any, path_id: str, communication_id: str,
                      destination: str) -> dict[str, Any]:
    """The source half's two receipts, as copies or as a ledger readback: sealed, linked, naming this path."""
    base = {"path_id": path_id, "communication_id": communication_id}
    if not sealed(org_receipt) or not sealed(repo_receipt):
        return refusal(FAIL_CLOSED, NOT_CARRIED, "SOURCE_PATH_RECEIPTS_ARE_SUPPLIED_SEALED",
                       "the source's sealed INTR_MESH_PATH_TRANSFER organization receipt and its sealed repository receipt",
                       "supply both receipts and re-run; nothing was appended", **base)
    boundary = mt._boundary(org_receipt)
    evidence = repo_receipt.get("evidence") if isinstance(repo_receipt.get("evidence"), Mapping) else {}
    problems = []
    if boundary.get("operation") != SOURCE_OPERATION or boundary.get("path_id") != path_id \
            or boundary.get("communication_id") != communication_id:
        problems.append("source organization receipt names " + repr((boundary.get("operation"), boundary.get("path_id"),
                                                                     boundary.get("communication_id"))))
    if org_receipt.get("repo_receipt_sha256") != repo_receipt["receipt_sha256"]:
        problems.append("source organization receipt does not link the supplied repository receipt")
    source_repo = org_receipt.get("source_repository", org_receipt.get("repository"))
    for field, want in (("schema", SOURCE_EVIDENCE_SCHEMA), ("path_id", path_id),
                        ("communication_id", communication_id), ("destination_organization", destination),
                        ("destination_address", address(destination)), ("repository", source_repo)):
        if evidence.get(field) != want:
            problems.append("source evidence " + field + " is " + repr(evidence.get(field)))
    if not _payload_ok(evidence):
        problems.append("source evidence does not carry the digest it claims")
    message = evidence.get("message")
    if sha(message) != evidence.get("message_sha256"):
        problems.append("message does not match message_sha256")
    elif ingress.shape_refusal(message) is not None:
        problems.append("message is refused by intr_destination_ingress: " + repr(ingress.shape_refusal(message)))
    elif isinstance(message, Mapping) and message.get("communication_id") != communication_id:
        problems.append("message communication_id differs")
    if problems:
        return refusal(DENY, CARRIED_CONTRADICTED, "SOURCE_PATH_RECEIPTS_NAME_THIS_DESTINATION",
                       "the source receipts must name this path, communication_id, destination and address, link each "
                       "other, and carry an ingress-acceptable message",
                       "inspect the source receipts; nothing was appended", **base, detail="; ".join(problems))
    return {"schema": RESULT_SCHEMA, "disposition": ALLOW, **base, "source_repository": source_repo,
            "source_organization_receipt_sha256": org_receipt["receipt_sha256"],
            "source_repository_receipt_sha256": repo_receipt["receipt_sha256"], "message": message}


def _purpose(path_id: str, other_repository: str) -> str:
    """The TVC scope purpose for reading ``other_repository``'s ledger on this path."""
    source = str(path_id).split(":")[0].split("->")[0]
    return "source_inclusion" if str(other_repository).split("/")[0] == source else "destination_readback"


def _reader(path_id: str, purpose: str) -> str:
    ends = str(path_id).split(":")[0].split("->")
    return ends[1] if purpose == "source_inclusion" and len(ends) > 1 else ends[0]


def inclusion_refusal(other_repository: str, path_id: str, communication_id: str, **extra: Any) -> dict[str, Any]:
    """FAIL_CLOSED until the TVC read receipt for this scope is observed; retry at the TVC transition."""
    purpose = _purpose(path_id, other_repository)
    return {"schema": RESULT_SCHEMA, **tvc_read.not_issued(path_id, purpose, str(other_repository), **extra),
            "communication_id": communication_id}


def tvc_readback(path_id: str, communication_id: str, other_repository: str,
                 directory: Path | str | None = None) -> tuple[Any, dict[str, Any] | None]:
    """The other organization's ledger readback from its verified TVC read receipt, or a six-field refusal."""
    purpose = _purpose(path_id, other_repository)
    rb, refused = tvc_read.lookup(path_id, purpose, _reader(path_id, purpose), str(other_repository),
                                  directory=directory or tvc_read.RECEIPT_DIR, allow_fixture=TVC_FIXTURE_OK)
    if refused is not None:
        return None, {"schema": RESULT_SCHEMA, **refused, "communication_id": communication_id}
    return rb, None


def source_inclusion(checked: Mapping[str, Any], directory: Path | str | None = None) -> dict[str, Any]:
    """The carried source organization receipt on the source chain, read through TV/TVC."""
    pid, cid, src = checked["path_id"], checked["communication_id"], checked["source_repository"]
    rb, refused = tvc_readback(pid, cid, src, directory)
    if refused is not None:
        return refused
    carried = {"receipt_sha256": checked["source_organization_receipt_sha256"]}
    found = rb.get("organization_receipt") if isinstance(rb.get("organization_receipt"), Mapping) else {}
    found_repo = rb.get("repository_receipt") if isinstance(rb.get("repository_receipt"), Mapping) else {}
    if found.get("receipt_sha256") != carried["receipt_sha256"] or not _included(rb, found) \
            or found_repo.get("receipt_sha256") != checked["source_repository_receipt_sha256"]:
        return refusal(DENY, SOURCE_LINK_UNVERIFIED, "SOURCE_RECEIPT_IS_INCLUDED_IN_THE_SOURCE_LEDGER",
                       "the carried source receipts must be the ones on the source chain read through TV/TVC",
                       "inspect the carried copies against the source ledger; the carried pair is not verified",
                       path_id=pid, communication_id=cid, source_ledger_tip=rb.get("ledger_tip"))
    return {"schema": RESULT_SCHEMA, "disposition": ALLOW, "state": "VERIFIED_BY_TVC_READ_RECEIPT",
            "path_id": pid, "communication_id": cid, "source_ledger_tip": rb.get("ledger_tip"),
            "source_ledger_head_epoch": rb.get("ledger_head_epoch"),
            "tvc_read_receipt": tvc_read.receipt_file(pid, "source_inclusion", ".").name}


# -- consume (append) -----------------------------------------------------------

def load_inbound(path: Path | str = INBOUND) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    document = json.loads(p.read_text(encoding="utf-8"))
    if document.get("schema") != INBOUND_SCHEMA or document.get("destination_repository") != repository():
        raise SystemExit("FAIL_CLOSED: INTR_PATH_INBOUND_INVALID " + str(path))
    return list(document["transfers"])


def ingress_evidence(checked: Mapping[str, Any], inclusion: Mapping[str, Any] | None = None) -> dict[str, Any]:
    core = {"schema": EVIDENCE_SCHEMA, "repository": repository(), "path_id": checked["path_id"],
            "source_repository": checked["source_repository"],
            "source_organization_receipt_sha256": checked["source_organization_receipt_sha256"],
            "source_repository_receipt_sha256": checked["source_repository_receipt_sha256"],
            "message": checked["message"], "message_sha256": sha(checked["message"]),
            "addressed_service": address(organization()),
            "source_inclusion": ({"state": inclusion["state"], "source_ledger_tip": inclusion["source_ledger_tip"],
                                  "tvc_read_receipt": inclusion["tvc_read_receipt"]}
                                 if inclusion and inclusion.get("disposition") == ALLOW
                                 else "UNOBSERVED_WITHOUT_A_CROSS_ORG_READ_CREDENTIAL")}
    return {**core, "payload_sha256": sha(core), "disposition": ALLOW,
            "communication_id": checked["communication_id"]}


def find_destination(path_id: str, communication_id: str, organization_store: Any = None,
                     repository_store: Any = None) -> tuple[Any, Any, str | None]:
    organization_store = organization_store if organization_store is not None else mt.org_store()
    repository_store = repository_store if repository_store is not None else mt.repo_store()
    for digest, receipt in mt.org_chain(organization_store):
        if not sealed(receipt):
            return None, None, "organization receipt " + digest + " on the chain is absent or unsealed"
        b = mt._boundary(receipt)
        if b.get("operation") == OPERATION and b.get("path_id") == path_id and b.get("communication_id") == communication_id:
            return receipt, mt._receipt(repository_store, receipt.get("repo_receipt_sha256")), None
    return None, None, None


def consume(inbound: Path | str = INBOUND) -> list[dict[str, Any]]:
    rows = load_inbound(inbound)
    if not rows:
        return []
    out = []
    for row in rows:
        pid, cid = row.get("path_id"), row.get("communication_id")
        carried = row.get("carried") if isinstance(row.get("carried"), Mapping) else {}
        checked = check_source_pair(carried.get("source_organization_receipt"), carried.get("source_repository_receipt"),
                                    pid, cid, organization())
        if checked["disposition"] != ALLOW:
            out.append(checked)
            continue
        existing, existing_repo, _ = find_destination(pid, cid)
        if existing is not None:
            ok = verify_destination_pair(existing, existing_repo, checked)
            out.append({**ok, "replayed": True} if ok["disposition"] == ALLOW else ok)
            continue
        epoch, failed = mt.head_epoch(mt.org_store())   # read only when a row is to be appended
        if failed is not None:
            out.append({**failed, "schema": RESULT_SCHEMA, "path_id": pid, "communication_id": cid})
            continue
        inclusion = source_inclusion(checked)
        evidence = ingress_evidence(checked, inclusion)
        try:
            appended, replayed = ingress._append(
                TRANSITION_CLASS, pid + "@" + cid, evidence, epoch, operation=OPERATION, profile_id=ingress.PROFILE_ID,
                boundary_extra={"path_id": pid, "source_repository": checked["source_repository"],
                                "source_organization_receipt_sha256": checked["source_organization_receipt_sha256"],
                                "source_repository_receipt_sha256": checked["source_repository_receipt_sha256"]})
        except (ValueError, SystemExit) as exc:
            out.append(refusal(FAIL_CLOSED, mt.WRITER_REFUSED, "LEDGER_WRITER_ADMITS_THE_APPEND",
                               "repair what the ledger writer refused: " + str(exc),
                               "re-run once repaired; nothing was written for this row", path_id=pid, communication_id=cid))
            continue
        out.append({"schema": RESULT_SCHEMA, "disposition": ALLOW, "half": "destination", "path_id": pid,
                    "communication_id": cid, "hb_epoch": epoch, "replayed": replayed,
                    "organization_receipt_sha256": appended["organization_receipt"]["receipt_sha256"],
                    "repository_receipt_sha256": appended["repository_receipt"]["receipt_sha256"],
                    "source_inclusion": inclusion})
    return out


def verify_inbound(inbound: Path | str = INBOUND) -> list[dict[str, Any]]:
    """Each inbound row against this ledger: the destination pair must be ledgered and link the carried seals."""
    out = []
    for row in load_inbound(inbound):
        pid, cid = row.get("path_id"), row.get("communication_id")
        carried = row.get("carried") if isinstance(row.get("carried"), Mapping) else {}
        checked = check_source_pair(carried.get("source_organization_receipt"), carried.get("source_repository_receipt"),
                                    pid, cid, organization())
        if checked["disposition"] != ALLOW:
            out.append(checked)
            continue
        found, found_repo, broken = find_destination(pid, cid)
        result = verify_destination_pair(found, found_repo, checked)
        out.append({**result, "detail": broken} if broken else result)
    return out


# -- verify ---------------------------------------------------------------------

def verify_destination_pair(org_receipt: Any, repo_receipt: Any, source: Mapping[str, Any],
                            destination_repository: str | None = None) -> dict[str, Any]:
    """This ledger's ingress receipts for the path: sealed, linked, and linking the source receipts by seal."""
    destination_repository = destination_repository or repository()
    pid, cid = source["path_id"], source["communication_id"]
    base = {"path_id": pid, "communication_id": cid, "half": "destination"}
    if not sealed(org_receipt) or not sealed(repo_receipt):
        return refusal(FAIL_CLOSED, NOT_LEDGERED, "PATH_HAS_A_DESTINATION_LEDGER_RECEIPT",
                       "a sealed " + OPERATION + " organization receipt for " + pid + " / " + cid + " on "
                       + destination_repository + "'s ledger, linked to its sealed repository receipt",
                       "receipts appear only after the inbound row lands on " + destination_repository
                       + " main and " + ENTRYPOINT + " consumes it", **base)
    b = mt._boundary(org_receipt)
    evidence = repo_receipt.get("evidence") if isinstance(repo_receipt.get("evidence"), Mapping) else {}
    problems = []
    if org_receipt.get("repo_receipt_sha256") != repo_receipt["receipt_sha256"]:
        problems.append("destination organization receipt does not link its repository receipt")
    if org_receipt.get("source_repository", org_receipt.get("repository")) != destination_repository:
        problems.append("destination receipt repository is " + repr(org_receipt.get("source_repository")))
    for field in ("source_organization_receipt_sha256", "source_repository_receipt_sha256"):
        if b.get(field) != source[field] or evidence.get(field) != source[field]:
            problems.append("destination receipts link " + field + " " + repr(b.get(field)))
    if b.get("operation") != OPERATION or b.get("path_id") != pid or b.get("communication_id") != cid \
            or evidence.get("schema") != EVIDENCE_SCHEMA or evidence.get("communication_id") != cid \
            or evidence.get("path_id") != pid or not _payload_ok(evidence):
        problems.append("destination receipts do not name this path, schema and digest")
    if problems:
        return refusal(DENY, LINK_UNVERIFIED, "DESTINATION_RECEIPT_LINKS_THE_SOURCE_RECEIPT",
                       "the destination ingress receipts must link the source path receipts by seal",
                       "inspect the destination receipts; nothing was verified", **base, detail="; ".join(problems))
    return {"schema": RESULT_SCHEMA, "disposition": ALLOW, **base,
            "organization_receipt_sha256": org_receipt["receipt_sha256"],
            "repository_receipt_sha256": repo_receipt["receipt_sha256"]}


def _included(readback: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
    """The receipt is on the readback's chain: walk from the readback head through previous_receipt_sha256."""
    chain = {r.get("receipt_sha256"): r for r in readback.get("chain", []) if isinstance(r, Mapping)}
    digest, seen = readback.get("head_receipt_sha256"), set()
    while isinstance(digest, str) and digest not in seen:
        seen.add(digest)
        r = chain.get(digest)
        if not sealed(r) or r["receipt_sha256"] != digest:
            return False
        if digest == receipt.get("receipt_sha256"):
            return True
        digest = r.get("previous_receipt_sha256")
    return False


def verify_end_to_end(path_id: str, communication_id: str, source_readback: Any,
                      destination_readback: Any) -> dict[str, Any]:
    """ALLOW only from readbacks of both ledgers (stegverse.intr-path-ledger-readback/v1): source receipts on the
    source chain, destination receipts on the destination chain, linked by seal."""
    base = {"path_id": path_id, "communication_id": communication_id, "half": "end_to_end"}
    readbacks = []
    for name, rb in (("source", source_readback), ("destination", destination_readback)):
        if not isinstance(rb, Mapping) or rb.get("schema") != READBACK_SCHEMA:
            ends = str(path_id).split(":")[0].split("->")
            org = ends[0] if name == "source" else (ends[1] if len(ends) > 1 else "<destination>")
            return {**inclusion_refusal(org + "/.github", path_id, communication_id), "half": name, "path": "UNVERIFIED"}
        readbacks.append(rb)
    src, dst = readbacks
    s_org, s_rep = src.get("organization_receipt"), src.get("repository_receipt")
    if not sealed(s_org) or not _included(src, s_org):
        return refusal(FAIL_CLOSED, SOURCE_NOT_LEDGERED, "PATH_HAS_A_SOURCE_LEDGER_RECEIPT",
                       "the source path receipt, included in the source ledger readback chain",
                       "append the source half on the source's main, read it back and re-verify", **base, path="UNVERIFIED")
    destination_org = dst.get("repository", "").split("/")[0]
    source = check_source_pair(s_org, s_rep, path_id, communication_id, destination_org)
    if source["disposition"] != ALLOW:
        return {**source, **base, "path": "UNVERIFIED"}
    if src.get("repository") != source["source_repository"]:
        return refusal(DENY, SOURCE_LINK_UNVERIFIED, "PATH_HAS_A_SOURCE_LEDGER_RECEIPT",
                       "the source readback must be the ledger of the repository the source receipt names",
                       "read back the right ledger; nothing was verified", **base, path="UNVERIFIED")
    d_org, d_rep = dst.get("organization_receipt"), dst.get("repository_receipt")
    if not sealed(d_org) or not _included(dst, d_org):
        return {**verify_destination_pair(None, None, source, dst.get("repository")), **base, "path": "UNVERIFIED"}
    destination = verify_destination_pair(d_org, d_rep, source, dst.get("repository"))
    if destination["disposition"] != ALLOW:
        return {**destination, **base, "path": "UNVERIFIED"}
    return {"schema": RESULT_SCHEMA, "disposition": ALLOW, "path": "VERIFIED", **base,
            "source_repository": src["repository"], "destination_repository": dst["repository"],
            "source_ledger_tip": src.get("ledger_tip"), "destination_ledger_tip": dst.get("ledger_tip"),
            "source_organization_receipt_sha256": s_org["receipt_sha256"],
            "destination_organization_receipt_sha256": d_org["receipt_sha256"]}


def readback(path_id: str, communication_id: str, operation: str, ledger_tip: str | None = None) -> dict[str, Any]:
    """Read this repository's own ledger (the bound stores) into a stegverse.intr-path-ledger-readback/v1 object."""
    org, rep = mt.org_store(), mt.repo_store()
    chain = [r for _, r in mt.org_chain(org) if isinstance(r, Mapping)]
    found = next((r for r in chain if mt._boundary(r).get("operation") == operation
                  and mt._boundary(r).get("path_id") == path_id
                  and mt._boundary(r).get("communication_id") == communication_id), None)
    head = chain[0]["receipt_sha256"] if chain else None
    if found is not None:
        cut = [r for r in chain]
        chain = cut[:cut.index(found) + 1]
    return {"schema": READBACK_SCHEMA, "repository": repository(), "ledger_tip": ledger_tip,
            "head_receipt_sha256": head, "chain": chain if found is not None else [],
            "organization_receipt": found,
            "repository_receipt": mt._receipt(rep, found.get("repo_receipt_sha256")) if found else None}


def end_to_end_via_tvc(path_id: str, communication_id: str, directory: Path | str | None = None,
                       ledger_tip: str | None = None) -> dict[str, Any]:
    """This organization's half from its own ledger; the other organization's from its verified TVC read receipt."""
    ends = str(path_id).split(":")[0].split("->")
    if len(ends) != 2 or organization() not in ends:
        return refusal(FAIL_CLOSED, NOT_LEDGERED, "THIS_ORGANIZATION_IS_AN_END_OF_THE_PATH",
                       "run the end-to-end check in the source or destination organization of " + str(path_id),
                       "re-run in an end organization", path_id=path_id, communication_id=communication_id)
    here_is_source = organization() == ends[0]
    other = (ends[1] if here_is_source else ends[0]) + "/.github"
    remote, refused = tvc_readback(path_id, communication_id, other, directory)
    if refused is not None:   # the other organization's half first: nothing local is read without it
        return {**refused, "half": "destination" if here_is_source else "source", "path": "UNVERIFIED"}
    try:
        local = readback(path_id, communication_id, SOURCE_OPERATION if here_is_source else OPERATION, ledger_tip)
    except Refused as exc:
        return {**exc.result, "schema": RESULT_SCHEMA, "path_id": path_id, "communication_id": communication_id}
    except Exception as exc:  # the bound ledger location is unavailable in this checkout
        return refusal(FAIL_CLOSED, NOT_LEDGERED, "THIS_ORGANIZATION_LEDGER_IS_READABLE",
                       "this organization's ledger at its declared location: " + type(exc).__name__ + ": " + str(exc)[:200],
                       "re-run where the ledger location is materialized", path_id=path_id,
                       communication_id=communication_id, path="UNVERIFIED")
    src, dst = (local, remote) if here_is_source else (remote, local)
    return verify_end_to_end(path_id, communication_id, src, dst)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("consume")
    c.add_argument("--inbound", default=INBOUND)
    c.add_argument("--out")
    v = sub.add_parser("verify")
    v.add_argument("--inbound", default=INBOUND)
    v.add_argument("--out")
    r = sub.add_parser("readback")
    r.add_argument("--path-id", required=True)
    r.add_argument("--communication-id", required=True)
    r.add_argument("--operation", choices=(SOURCE_OPERATION, OPERATION), required=True)
    r.add_argument("--ledger-tip")
    r.add_argument("--out")
    e = sub.add_parser("end-to-end")
    e.add_argument("--path-id", required=True)
    e.add_argument("--communication-id", required=True)
    e.add_argument("--source-readback")
    e.add_argument("--destination-readback")
    e.add_argument("--out")
    t = sub.add_parser("end-to-end-via-tvc")
    t.add_argument("--path-id", required=True)
    t.add_argument("--communication-id", required=True)
    t.add_argument("--receipts", default=None)
    t.add_argument("--ledger-tip")
    t.add_argument("--out")
    args = parser.parse_args(argv)
    if args.command == "consume":
        result: Any = consume(args.inbound)
        code = 0 if all(x["disposition"] == ALLOW for x in result) else 1
    elif args.command == "verify":
        result = verify_inbound(args.inbound)
        code = 0 if all(x["disposition"] == ALLOW for x in result) else 1
    elif args.command == "readback":
        result = readback(args.path_id, args.communication_id, args.operation, args.ledger_tip)
        code = 0
    elif args.command == "end-to-end-via-tvc":
        result = end_to_end_via_tvc(args.path_id, args.communication_id, args.receipts, args.ledger_tip)
        code = 0 if result["disposition"] == ALLOW else 1
    else:
        load = lambda p: json.loads(Path(p).read_text(encoding="utf-8")) if p and Path(p).is_file() else None
        result = verify_end_to_end(args.path_id, args.communication_id, load(args.source_readback),
                                   load(args.destination_readback))
        code = 0 if result["disposition"] == ALLOW else 1
    text = json.dumps(result, indent=2, sort_keys=True)
    if getattr(args, "out", None):
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
