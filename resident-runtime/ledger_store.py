#!/usr/bin/env python3
"""Ledger storage addressed by key.

The caller supplies the location. A filesystem path is a directory of JSON
documents. ``git+<repository>#<ref>`` is one git ref, and an optional
``:<namespace>`` is a subtree of that ref. Two ledgers can share one ref: one commit history, one compare-and-swap,
one push, without their keys colliding. This module names no default location, repository, or ref.

Nothing here selects admission, routing, or execution. A disposition recorded
through this store is transition evidence. Authority and time stay constraint
variables on the receipt a caller writes.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path, PurePath
from typing import Any, Iterator

HEAD_KEY = "HEAD.json"
RECEIPT_PREFIX = "receipts/"
SOURCE_PREFIX = "source-receipts/"
GIT_SCHEME = "git+"


def _document_bytes(value: Any) -> bytes:
    """One serialization for every store, so a document reads back the same."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            + b"\n")


def receipt_key(digest: str) -> str:
    hex_digest = digest[7:] if digest.startswith("sha256:") else digest
    return RECEIPT_PREFIX + hex_digest + ".json"


def source_key(digest: str) -> str:
    hex_digest = digest[7:] if digest.startswith("sha256:") else digest
    return SOURCE_PREFIX + hex_digest + ".json"


class LedgerWriteFault(RuntimeError):
    """A fault in writing this store: a lost comparison, exhausted contention, or a failed git write.

    It is the writer's fault, never the content's. ``ledger_write_fault`` lets a
    caller holding another copy of this module match it without the class.
    """
    ledger_write_fault = True


class LedgerLocatorInvalid(ValueError):
    """A supplied ledger location that names no store. Nothing is opened or written."""

    failed_predicate = "LEDGER_LOCATOR_INVALID"

    def __init__(self, locator: str, detail: str) -> None:
        super().__init__("ledger_locator_invalid: " + detail + ": " + repr(locator))
        self.locator = locator
        self.detail = detail


class GitLocator:
    """A ledger addressed as a ref: ``git+<repository>#<ref>[:<namespace>]``."""

    def __init__(self, repository: Path, ref: str, namespace: str | None = None) -> None:
        self.repository = Path(repository)
        self.ref = ref
        self.namespace = namespace

    def __str__(self) -> str:
        text = GIT_SCHEME + str(self.repository) + "#" + self.ref
        return text + ":" + self.namespace if self.namespace else text

    def __repr__(self) -> str:
        return "GitLocator(" + repr(str(self)) + ")"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, GitLocator) and str(self) == str(other)

    def __hash__(self) -> int:
        return hash(str(self))


def _require_ref(ref: str, locator: str) -> str:
    if not ref.startswith("refs/"):
        raise LedgerLocatorInvalid(locator, "ref_must_be_fully_qualified_under_refs")
    checked = subprocess.run(["git", "check-ref-format", ref], capture_output=True)
    if checked.returncode != 0:
        raise LedgerLocatorInvalid(locator, "ref_is_not_a_valid_git_ref")
    return ref


_TOP_LEVEL_KEYS = ("HEAD.json", RECEIPT_PREFIX.rstrip("/"), SOURCE_PREFIX.rstrip("/"))


def _require_namespace(namespace: str, locator: str) -> str:
    """A namespace is a relative tree path that does not shadow a ledger key."""
    segments = namespace.split("/")
    if any(segment in ("", ".", "..") or any(c.isspace() or c in ":\\" for c in segment)
           for segment in segments):
        raise LedgerLocatorInvalid(locator, "namespace_is_not_a_relative_normalized_tree_path")
    if segments[0] in _TOP_LEVEL_KEYS:
        raise LedgerLocatorInvalid(locator, "namespace_may_not_shadow_a_ledger_key")
    return namespace


def parse_locator(value: str | Path | GitLocator) -> Path | GitLocator:
    """The store a supplied value names.

    ``git+<repository>#<ref>`` names a git ref. Both halves are required and
    the ref must be fully qualified. ``:<namespace>`` after the ref names a
    subtree. Anything else is a filesystem path.
    """
    if isinstance(value, GitLocator):
        return value
    if not isinstance(value, PurePath):
        text = str(value)
        if text.startswith(GIT_SCHEME):
            repository, separator, ref = text[len(GIT_SCHEME):].rpartition("#")
            if not separator or not repository or not ref:
                raise LedgerLocatorInvalid(text, "git_locator_requires_repository_and_ref")
            ref, has_namespace, namespace = ref.partition(":")
            if has_namespace and not namespace:
                raise LedgerLocatorInvalid(text, "namespace_separator_without_a_namespace")
            return GitLocator(
                Path(repository).expanduser().resolve(),
                _require_ref(ref, text),
                _require_namespace(namespace, text) if has_namespace else None)
    return Path(value)


def open_store(location: str | Path | GitLocator) -> Store | GitLedgerStore:
    """The store at a supplied location: a git ref for a git locator, else a directory."""
    if isinstance(location, GitLocator) or (
            not isinstance(location, PurePath) and str(location).startswith(GIT_SCHEME)):
        locator = location if isinstance(location, GitLocator) else parse_locator(str(location))
        if not isinstance(locator, GitLocator):
            raise LedgerLocatorInvalid(str(location), "git_locator_requires_repository_and_ref")
        return GitLedgerStore(locator.repository, locator.ref, locator.namespace)
    return Store(Path(location))


class Store:
    """A ledger store on one directory. The caller supplies the root."""

    kind = "POSIX_FILESYSTEM"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock_path = self.root / ".append.lock"
        self._held = 0

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key

    def locator(self, key: str) -> str:
        return str(self._path(key))

    def get(self, key: str) -> Any:
        path = self._path(key)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, key: str, document: Any) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_document_bytes(document))

    def list_prefix(self, prefix: str) -> set[str]:
        directory = self._path(prefix.rstrip("/"))
        if not directory.is_dir():
            return set()
        return {prefix + item.name for item in directory.glob("*.json")}

    @contextmanager
    def exclusive(self) -> Iterator[Store]:
        """Serialize appenders on this directory, re-entrantly for one holder."""
        if self._held:
            self._held += 1
            try:
                yield self
            finally:
                self._held -= 1
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self._held = 1
            try:
                yield self
            finally:
                self._held = 0

    def append_transaction(self, receipt_key_name: str, receipt: Any, expected_head: Any,
                           new_head: Any, immutable: dict[str, Any] | None = None) -> bool:
        """Publish a receipt and HEAD when HEAD still equals ``expected_head``.

        A lost comparison writes nothing. Content-addressed keys already
        holding a different document raise ``ledger_receipt_collision`` before
        any write. HEAD is written last.
        """
        documents = dict(immutable or {})
        documents[receipt_key_name] = receipt
        with self.exclusive():
            if self.get(HEAD_KEY) != expected_head:
                return False
            missing = []
            for key, value in documents.items():
                existing = self.get(key)
                if existing is not None and existing != value:
                    raise ValueError("ledger_receipt_collision")
                if existing is None:
                    missing.append(key)
            for key in missing:
                self.put(key, documents[key])
            self.put(HEAD_KEY, new_head)
            return True


class GitLedgerStore:
    """A ledger store on one ref of a git repository.

    Documents are blobs at the same keys a directory store uses as paths.
    Each transaction is one commit whose parent is the commit it was computed
    from, published with ``git update-ref <ref> <new> <old>``. A writer whose
    view went stale publishes nothing. The receipt and HEAD land in one
    commit, so a reader does not see a receipt HEAD does not name.

    The caller supplies the repository and the ref. This store does not
    choose either.
    """

    kind = "GIT_REF"
    _IDENTITY = {
        "GIT_AUTHOR_NAME": "stegverse-ledger-store",
        "GIT_AUTHOR_EMAIL": "ledger-store@stegverse.invalid",
        "GIT_COMMITTER_NAME": "stegverse-ledger-store",
        "GIT_COMMITTER_EMAIL": "ledger-store@stegverse.invalid",
    }
    _IMMUTABLE_PREFIXES = (RECEIPT_PREFIX, SOURCE_PREFIX)
    _ATTEMPTS = 128

    def __init__(self, repository: Path, ref: str, namespace: str | None = None) -> None:
        self.repository = Path(repository)
        text = GIT_SCHEME + str(repository) + "#" + str(ref) + ((":" + str(namespace)) if namespace else "")
        self.ref = _require_ref(ref, text)
        self.namespace = _require_namespace(namespace, text) if namespace else None
        self._local = threading.local()

    def _path(self, key: str) -> str:
        return self.namespace + "/" + key if self.namespace else key

    def _git_dir(self) -> Path | None:
        if (self.repository / "HEAD").is_file() and (self.repository / "objects").is_dir():
            return self.repository
        dotgit = self.repository / ".git"
        if dotgit.is_dir():
            return dotgit
        if dotgit.is_file():
            pointer = dotgit.read_text(encoding="utf-8").strip()
            if pointer.startswith("gitdir:"):
                return (self.repository / pointer[len("gitdir:"):].strip()).resolve()
        return None

    def _env(self, git_dir: Path, **extra: str) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(self._IDENTITY)
        env["GIT_DIR"] = str(git_dir)
        env.update(extra)
        return env

    def _git(self, *args: str, stdin: bytes | None = None, env: dict[str, str] | None = None,
             check: bool = True) -> subprocess.CompletedProcess[bytes]:
        git_dir = self._git_dir()
        if git_dir is None:
            raise FileNotFoundError("ledger repository not initialized: " + str(self.repository))
        completed = subprocess.run(
            ["git", *args], input=stdin, capture_output=True, env=env or self._env(git_dir))
        if check and completed.returncode != 0:
            detail = completed.stderr.decode(errors="replace").strip()
            raise LedgerWriteFault("git " + args[0] + " failed: " + detail)
        return completed

    def _tip(self) -> str | None:
        if self._git_dir() is None:
            return None
        completed = self._git("rev-parse", "--verify", "--quiet", self.ref + "^{commit}", check=False)
        if completed.returncode != 0:
            if self._git("show-ref", "--verify", "--quiet", self.ref, check=False).returncode == 0:
                raise ValueError("ledger_ref_does_not_name_a_commit: " + self.ref)
            return None
        return completed.stdout.decode().strip()

    def _read(self, revision: str | None, keys: list[str]) -> dict[str, bytes | None]:
        if revision is None or not keys:
            return {key: None for key in keys}
        request = "".join(revision + ":" + self._path(key) + "\n" for key in keys).encode()
        output = self._git("cat-file", "--batch", stdin=request).stdout
        found: dict[str, bytes | None] = {}
        offset = 0
        for key in keys:
            end = output.index(b"\n", offset)
            header = output[offset:end].decode().split(" ")
            offset = end + 1
            if header[-1] in ("missing", "ambiguous"):
                found[key] = None
                continue
            object_type, size = header[1], int(header[2])
            body = output[offset:offset + size]
            offset += size + 1
            if object_type != "blob":
                raise ValueError("ledger_key_is_not_a_document: " + key)
            found[key] = body
        return found

    def _load(self, revision: str | None, keys: list[str]) -> dict[str, Any]:
        return {key: None if raw is None else json.loads(raw)
                for key, raw in self._read(revision, keys).items()}

    def _commit(self, parent: str | None, documents: dict[str, bytes], message: str) -> str:
        git_dir = self._git_dir()
        if git_dir is None:
            raise FileNotFoundError("ledger repository not initialized: " + str(self.repository))
        with tempfile.TemporaryDirectory(prefix=".ledger-index-") as scratch:
            env = self._env(git_dir, GIT_INDEX_FILE=str(Path(scratch) / "index"))
            if parent is None:
                self._git("read-tree", "--empty", env=env)
            else:
                self._git("read-tree", parent, env=env)
            entries = []
            for key in sorted(documents):
                blob = self._git("hash-object", "-w", "--stdin", stdin=documents[key]).stdout.decode().strip()
                entries.append("100644 " + blob + "\t" + self._path(key) + "\n")
            self._git("update-index", "--index-info", stdin="".join(entries).encode(), env=env)
            tree = self._git("write-tree", env=env).stdout.decode().strip()
        if parent is not None and tree == self._git("rev-parse", parent + "^{tree}").stdout.decode().strip():
            return parent
        args = ["commit-tree", "--no-gpg-sign", tree, "-m", message]
        if parent is not None:
            args[2:2] = ["-p", parent]
        return self._git(*args).stdout.decode().strip()

    def _publish(self, new: str, expected: str | None) -> bool:
        old = expected if expected is not None else "0" * len(new)
        completed = self._git("update-ref", "-m", "ledger append", self.ref, new, old, check=False)
        if completed.returncode == 0:
            return True
        stderr = completed.stderr.decode(errors="replace")
        if self._tip() != expected or "File exists" in stderr:
            return False
        raise LedgerWriteFault("git update-ref failed: " + stderr.strip())

    def _refuse_replacing_immutable(self, current: bytes | None, key: str, value: Any) -> None:
        if key.startswith(self._IMMUTABLE_PREFIXES) and current is not None and json.loads(current) != value:
            raise ValueError("ledger_receipt_collision")

    def initialize(self) -> None:
        """Open the repository. The ref is created by the first append, not here."""
        if self._git_dir() is not None:
            return
        if self.repository.exists() and any(self.repository.iterdir()):
            if self._git_dir() is not None:
                return
            raise LedgerLocatorInvalid(str(self.repository), "location_is_not_a_git_repository")
        self.repository.parent.mkdir(parents=True, exist_ok=True)
        staging = tempfile.mkdtemp(prefix=".ledger-init-", dir=str(self.repository.parent))
        try:
            env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            subprocess.run(["git", "init", "--bare", "--quiet", staging],
                           check=True, capture_output=True, env=env)
            try:
                os.rename(staging, self.repository)
            except OSError:
                if self._git_dir() is None:
                    raise
        finally:
            if os.path.exists(staging):
                shutil.rmtree(staging, ignore_errors=True)

    def locator(self, key: str) -> str:
        return GIT_SCHEME + str(self.repository) + "#" + self.ref + ":" + self._path(key)

    def exists(self, key: str) -> bool:
        return self._read(self._tip(), [key])[key] is not None

    def get(self, key: str) -> Any:
        return self._load(self._tip(), [key])[key]

    def put(self, key: str, value: Any) -> None:
        encoded = _document_bytes(value)
        for _attempt in range(self._ATTEMPTS):
            tip = self._tip()
            self._refuse_replacing_immutable(self._read(tip, [key])[key], key, value)
            self.initialize()
            new = self._commit(tip, {key: encoded}, "ledger put " + key)
            if new == tip or self._publish(new, tip):
                return
        raise LedgerWriteFault("LEDGER_STORE_PUT_CONTENTION_EXHAUSTED")

    def list_prefix(self, prefix: str) -> set[str]:
        tip = self._tip()
        directory = self._path(prefix.rstrip("/"))
        if tip is None:
            return set()
        listed = self._git("ls-tree", "-z", "--name-only", tip, "--", directory + "/").stdout
        skip = len(self.namespace) + 1 if self.namespace else 0
        names = {name[skip:] for name in listed.decode().split("\0") if name}
        return {name for name in names
                if name.endswith(".json") and name.startswith(prefix) and "/" not in name[len(prefix):]}

    @contextmanager
    def exclusive(self) -> Iterator[GitLedgerStore]:
        held = getattr(self._local, "held", 0)
        if held:
            self._local.held = held + 1
            try:
                yield self
            finally:
                self._local.held -= 1
            return
        self.initialize()
        name = "stegverse-ledger-" + hashlib.sha256(self.ref.encode()).hexdigest()[:16] + ".lock"
        with (self._git_dir() / name).open("a+b") as lock:  # type: ignore[operator]
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self._local.held = 1
            try:
                yield self
            finally:
                self._local.held = 0

    def append_transaction(self, receipt_key_name: str, receipt: Any, expected_head: Any,
                           new_head: Any, immutable: dict[str, Any] | None = None) -> bool:
        """Publish a receipt and HEAD in one commit when HEAD still matches."""
        documents = dict(immutable or {})
        documents[receipt_key_name] = receipt
        with self.exclusive():
            for _attempt in range(self._ATTEMPTS):
                tip = self._tip()
                current = self._read(tip, [HEAD_KEY, *documents])
                head = current.pop(HEAD_KEY)
                if (None if head is None else json.loads(head)) != expected_head:
                    return False
                writes: dict[str, bytes] = {}
                for key, value in documents.items():
                    existing = current[key]
                    if existing is not None and json.loads(existing) != value:
                        raise ValueError("ledger_receipt_collision")
                    if existing is None:
                        writes[key] = _document_bytes(value)
                writes[HEAD_KEY] = _document_bytes(new_head)
                new = self._commit(tip, writes, "ledger append " + receipt_key_name)
                if self._publish(new, tip):
                    return True
        raise LedgerWriteFault("LEDGER_STORE_APPEND_CONTENTION_EXHAUSTED")
