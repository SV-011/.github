# SV-011 entity package v0 (experiment 1)

The smallest data package that creates an AI Entity governed by design. Stdlib only.

- `package-manifest.v0.json`: the only thing the entity loads from. It lists three Admissible-Existence components (repo@commit:path + git blob sha1), the declared actions (`PROPOSE`, `TRANSITION`, `VERIFY`) and the declared transition classes (`GENESIS`, `ENTITY_STATE_UPDATE`).
- `components/`: byte-exact copies of those AE files.
- `sv011_entity/`: the bootstrap. It acts only by manifest and proposes. A transition needs an ALLOW from GTG-A1..A8. It is state-dependent (a request must name the current state digest) and keeps its own hash-chained receipt record (`<record>/receipts`, `<record>/states`). Every non-ALLOW carries the six fields.
- `MINIMALITY.md`: for each component, what breaks without it, plus not-present records.

Run (Python 3.10+):

```
cd sv011-entity-package
python3 -m sv011_entity load --package .
python3 -m sv011_entity run --package . --record /path/to/record --request examples/genesis-request.json
python3 -m sv011_entity run --package . --record /path/to/record --request examples/update-request.json
python3 -m sv011_entity run --package . --record /path/to/record --request examples/verify-request.json
python3 -m unittest discover -s tests -v
```

Exit code 0 means ALLOW. 2 means DENY or FAIL_CLOSED, with the record printed as JSON.

Next experiment (out of scope here): KV infrastructure (`_StegVerse/` tree, storage endpoint / access adapter, SKAP Vault, KV and StegOS boundaries through a healthy node). This entity later becomes the assistant package for a new KV install.
