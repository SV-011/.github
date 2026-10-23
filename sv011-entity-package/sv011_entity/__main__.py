"""CLI: python3 -m sv011_entity load --package DIR | run --package DIR --record DIR --request FILE"""
from __future__ import annotations

import argparse
import json
import sys

from . import bootstrap, entity
from .dispositions import allow


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sv011_entity")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ld = sub.add_parser("load")
    ld.add_argument("--package", required=True)
    rn = sub.add_parser("run")
    rn.add_argument("--package", required=True)
    rn.add_argument("--record", required=True)
    rn.add_argument("--request")
    a = ap.parse_args(argv)
    if a.cmd == "load":
        gov, bad = bootstrap.load(a.package)
        out = bad or allow(entity_id=gov["manifest"]["entity_id"], roles=list(bootstrap.REQUIRED_ROLES))
    else:
        out = entity.run(a.package, a.record, a.request)
    print(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if out["disposition"] == "ALLOW" else 2


if __name__ == "__main__":
    sys.exit(main())
