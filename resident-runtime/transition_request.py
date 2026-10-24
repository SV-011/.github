#!/usr/bin/env python3
"""``COMMITTED_REQUEST`` — the intr-destination self-closure request a push to main carries.

The organization ledger transition writer
(.github/workflows/organization-ledger-transition-writer.yml) runs on the push
to main that commits a request file under ``.stegverse/transition-ledger/requests/``.
It has no dispatch and no inputs: the ids and the epoch are read from the
committed file, the lower bound on the epoch from the ledger head. Nothing reads
a clock and nothing mints an epoch; there is no default epoch.

A request is ``{"schema": SCHEMA, "communication_id", "packet_id", "hb_epoch"}``.
Absent means NOT_SUPPLIED and contradicted means INVALID, each a six-field
refusal; nothing is written either way:

  NOT_SUPPLIED TRANSITION_REQUEST_FIELD_NOT_SUPPLIED: a field is missing.
  INVALID TRANSITION_REQUEST_INVALID: the file is not one JSON object of SCHEMA,
    an id is not a printable token, hb_epoch is not an int >= 32, the epoch is
    before the ledger head's supplied epoch, or the push carries more than one
    request.

An accepted request carries ``request_sha256`` (sha256 of the committed bytes),
recorded in the writer run's request.json evidence and step summary. Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

REQUEST_DIR = ".stegverse/transition-ledger/requests"
SCHEMA = "stegverse.org-ledger-transition-request/v1"
FIELDS = ("communication_id", "packet_id", "hb_epoch")
ENTRYPOINT = ".github/workflows/organization-ledger-transition-writer.yml (push to main of " + REQUEST_DIR + "/**)"
GOAL = "SVORG-LLM-ORG-FOUNDATION-001"

ALLOW, NOT_SUPPLIED, INVALID = "ALLOW", "NOT_SUPPLIED", "INVALID"
FIELD_NOT_SUPPLIED = "TRANSITION_REQUEST_FIELD_NOT_SUPPLIED"
REQUEST_INVALID = "TRANSITION_REQUEST_INVALID"
NO_REQUEST = "NO_REQUEST"

ZERO = re.compile(r"0{40}")


def refusal(disposition: str, failure_code: str, failed_predicate: str, repair: str,
            detail: str, path: str | None = None) -> dict[str, Any]:
    return {
        "disposition": disposition,
        "failure_code": failure_code,
        "failed_predicate": failed_predicate,
        "required_evidence_or_repair": repair,
        "retry_entrypoint": ENTRYPOINT,
        "owning_existing_goal": GOAL,
        "next_attempt": "commit a corrected request file to main; nothing was written",
        "detail": detail,
        "request_path": path,
        "production_receipt_claimed": False,
    }


def _token(value: Any) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 240
            and all(32 <= ord(character) <= 126 for character in value))


def gate(raw: bytes, path: str, head_epoch: int | None) -> dict[str, Any]:
    """ALLOW with the request's ids, epoch and digest, or a six-field refusal."""
    def invalid(predicate: str, detail: str) -> dict[str, Any]:
        return refusal(INVALID, REQUEST_INVALID, predicate,
                       "one JSON object of schema " + SCHEMA + " with printable communication_id and packet_id "
                       "and hb_epoch an int >= 32, not before the ledger head epoch", detail, path)
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return invalid("REQUEST_IS_ONE_JSON_OBJECT", str(exc))
    if not isinstance(request, Mapping):
        return invalid("REQUEST_IS_ONE_JSON_OBJECT", type(request).__name__)
    if request.get("schema") != SCHEMA:
        return invalid("REQUEST_DECLARES_THE_SCHEMA", repr(request.get("schema")))
    missing = [name for name in FIELDS if name not in request]
    if missing:
        return refusal(NOT_SUPPLIED, FIELD_NOT_SUPPLIED, "REQUEST_SUPPLIES_" + "_".join(FIELDS).upper(),
                       "add " + ", ".join(missing) + " to the request; there is no default",
                       "not supplied: " + ", ".join(missing), path)
    for name in ("communication_id", "packet_id"):
        if not _token(request[name]):
            return invalid(name.upper() + "_IS_A_PRINTABLE_TOKEN", repr(request[name]))
    epoch = request["hb_epoch"]
    if type(epoch) is not int or epoch < 32:
        return invalid("HB_EPOCH_IS_A_SUPPLIED_INT_AT_LEAST_32", repr(epoch))
    if head_epoch is not None and epoch < head_epoch:
        return invalid("HB_EPOCH_NOT_BEFORE_THE_LEDGER_HEAD",
                       "hb_epoch " + str(epoch) + " is before the ledger head epoch " + str(head_epoch))
    return {
        "disposition": ALLOW,
        "request_path": path,
        "request_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "communication_id": request["communication_id"],
        "packet_id": request["packet_id"],
        "hb_epoch": epoch,
        "ledger_head_epoch": head_epoch,
    }


def _git(*args: str) -> str:
    return subprocess.run(("git",) + args, check=True, capture_output=True, text=True).stdout


def changed_requests(before: str, after: str) -> list[str]:
    """Request files the push from ``before`` to ``after`` adds or modifies."""
    if not before or ZERO.fullmatch(before):
        listed = _git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "--diff-filter=AM",
                      after, "--", REQUEST_DIR)
    else:
        listed = _git("diff", "--name-only", "--diff-filter=AM", before, after, "--", REQUEST_DIR)
    return sorted(line for line in listed.splitlines() if line.endswith(".json"))


def ledger_head_epoch() -> int | None:
    """The supplied epoch on the organization ledger head; None when the ledger has none yet."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import merge_transfer  # noqa: E402  (loads the ledger writers the ingress uses)
    epoch, _ = merge_transfer.head_epoch(merge_transfer.org_store())
    return epoch


def select(before: str, after: str, head_epoch: int | None) -> dict[str, Any]:
    paths = changed_requests(before, after)
    if not paths:
        return {"disposition": NO_REQUEST, "detail": "the push adds or modifies no request file",
                "production_receipt_claimed": False}
    if len(paths) > 1:
        return refusal(INVALID, REQUEST_INVALID, "ONE_REQUEST_PER_PUSH",
                       "commit one request file per push", "requests: " + ", ".join(paths))
    raw = subprocess.run(("git", "show", after + ":" + paths[0]), check=True, capture_output=True).stdout
    return gate(raw, paths[0], head_epoch)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = select(args.before, args.after, ledger_head_epoch())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["disposition"] in (ALLOW, NO_REQUEST) else 1


if __name__ == "__main__":
    sys.exit(main())
