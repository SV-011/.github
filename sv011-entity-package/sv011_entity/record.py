"""The entity's own hash-chained receipt record (shape fixed by the declared AE transition contract)."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .dispositions import allow, non_allow

NAME = re.compile(r"^(\d{6})\.receipt\.json$")
VERIFY_RETRY = "python3 -m sv011_entity run --package <package_dir> --record <record_dir> --request <verify-request.json>"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_of(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


NULL_STATE_SHA256 = sha256_of(None)


def seal(body: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in body.items() if k != "receipt_sha256"}
    return {**body, "receipt_sha256": sha256_of(body)}


def _write_new(path: Path, payload: Any) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    with open(tmp, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_all(record_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Verify and return the chain; second item is a FAIL_CLOSED record on any defect."""
    def fc(code: str, pred: str) -> tuple[list, dict]:
        return [], non_allow("FAIL_CLOSED", code, pred, "restore the receipt record from a verified copy",
                             "no proposal or transition runs until the record verifies", retry=VERIFY_RETRY)
    rdir = record_dir / "receipts"
    if not rdir.exists():
        return [], None
    names = sorted(p.name for p in rdir.iterdir())
    if any(not NAME.match(n) for n in names):
        return fc("SV011_ENTITY_RECORD_FOREIGN_ENTRY", "receipts/ holds only NNNNNN.receipt.json")
    chain: list[dict[str, Any]] = []
    for i, name in enumerate(names):
        if int(NAME.match(name).group(1)) != i:
            return fc("SV011_ENTITY_RECORD_GAP", f"receipt sequence contiguous from 0 ({name})")
        try:
            r = json.loads((rdir / name).read_text(encoding="utf-8"))
        except ValueError:
            return fc("SV011_ENTITY_RECORD_UNREADABLE", f"{name} is JSON")
        if not isinstance(r, dict) or seal(r)["receipt_sha256"] != r.get("receipt_sha256"):
            return fc("SV011_ENTITY_RECORD_HASH_MISMATCH", f"{name} receipt_sha256 equals sha256 of its canonical body")
        prev = chain[-1]["receipt_sha256"] if chain else None
        if r.get("sequence") != i or r.get("previous_receipt_sha256") != prev:
            return fc("SV011_ENTITY_RECORD_LINK_BROKEN", f"{name} sequence == {i} and previous_receipt_sha256 == {prev}")
        expected_pred = chain[-1]["successor_state_sha256"] if chain else NULL_STATE_SHA256
        if r.get("predecessor_state_sha256") != expected_pred:
            return fc("SV011_ENTITY_RECORD_STATE_DISCONTINUOUS", f"{name} predecessor_state_sha256 == {expected_pred}")
        state_file = record_dir / "states" / (str(r.get("successor_state_sha256", "")).split(":", 1)[-1] + ".json")
        try:
            if sha256_of(json.loads(state_file.read_text(encoding="utf-8"))) != r.get("successor_state_sha256"):
                return fc("SV011_ENTITY_STATE_HASH_MISMATCH", f"state snapshot for {name} matches successor_state_sha256")
        except (OSError, ValueError):
            return fc("SV011_ENTITY_STATE_ABSENT", f"state snapshot for {name} is present and JSON")
        chain.append(r)
    return chain, None


def verify(record_dir: Path) -> dict[str, Any]:
    chain, bad = read_all(record_dir)
    if bad:
        return bad
    return allow(chain_length=len(chain), head_receipt_sha256=chain[-1]["receipt_sha256"] if chain else None)


def append(record_dir: Path, chain: list[dict[str, Any]], body: dict[str, Any], successor_state: Any) -> dict[str, Any]:
    (record_dir / "receipts").mkdir(parents=True, exist_ok=True)
    (record_dir / "states").mkdir(parents=True, exist_ok=True)
    state_path = record_dir / "states" / (body["successor_state_sha256"].split(":", 1)[1] + ".json")
    if not state_path.exists():
        _write_new(state_path, successor_state)
    seq = len(chain)
    receipt = seal({**body, "sequence": seq, "previous_receipt_sha256": chain[-1]["receipt_sha256"] if chain else None})
    _write_new(record_dir / "receipts" / f"{seq:06d}.receipt.json", receipt)
    return receipt
