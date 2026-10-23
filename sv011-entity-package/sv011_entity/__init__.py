"""SV-011 experiment 1: the smallest data package that creates an AI Entity governed by design.

Stdlib only. The entity loads only from package-manifest.v0.json, acts only on actions that
manifest declares, proposes, and takes a transition only on an ALLOW from the declared
predicates, recording each transition in its own hash-chained receipt record.
"""
PACKAGE_VERSION = "0.1.0"
