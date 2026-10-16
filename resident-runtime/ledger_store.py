#!/usr/bin/env python3
"""Storage for an append-only organization ledger, addressed rather than located.

The ledger's own model is already substrate-neutral: receipts are content
addressed and hash linked, and the chain is verified by recomputing digests.
What bound it to one machine was its storage, not its model -- a POSIX
filesystem under a home directory, a `fcntl` advisory lock as the whole
concurrency model, same-filesystem atomic rename for durability, and
directory enumeration as the integrity check. None of those survive a move to
ephemeral nodes that share no filesystem.

This module is the seam. The ledger addresses documents by key; a store maps
keys onto whatever substrate it has. `PosixLedgerStore` keeps today's exact
behaviour, so the filesystem remains a first-class implementation rather than
a legacy path, and a key-value store is a sibling rather than a rewrite.
`GitLedgerStore` is that sibling over a git ref: one commit per transaction,
published by `git update-ref`'s native compare-and-swap.

Keys are `HEAD`, `receipts/<hex>` and `source-receipts/<hex>`. Nothing here
satisfies an authority constraint. Authority and time stay constraint
variables on the documents a caller writes.

Ported from StegVerse-org/.github resident-runtime/ledger_store.py at
43ef7311f624291c9db55628473c8146b7815f07. The organization identity is not
named here; the caller supplies the location.

A store is selected only by the location the materializer supplies. A plain
path is a POSIX root, as it always was; `git+<repository>#<ref>` is a git
ref. This module names no default location, repository or ref, and no
location is the organization's ledger because a store can be opened on it:
designating the organization's sovereign ledger root is an owner decision,
pending on StegVerse-org/LLM-adapter#368.
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

HEAD_KEY = "HEAD.json"
RECEIPT_PREFIX = "receipts/"
SOURCE_PREFIX = "source-receipts/"


def receipt_key(digest):
    """Address a receipt by its own digest, so the key carries the content.

    The key keeps the `.json` suffix a filesystem store would give the file
    anyway, so a POSIX store's paths are unchanged by this indirection and an
    existing ledger root stays readable.
    """
    return RECEIPT_PREFIX + digest.split(":", 1)[1] + ".json"


def source_key(digest):
    """Address an exact retained source transition receipt by its source digest."""
    return SOURCE_PREFIX + digest.split(":", 1)[1] + ".json"


def _document_bytes(value):
    """One serialization for every store, so a document reads back byte-identical."""
    return json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"


GIT_SCHEME = "git+"


class LedgerLocatorInvalid(ValueError):
    """A supplied ledger location that names no store; nothing is opened or written."""

    failed_predicate = "LEDGER_LOCATOR_INVALID"

    def __init__(self, locator, detail):
        super().__init__("ledger_locator_invalid: " + detail + ": " + repr(locator))
        self.locator = locator
        self.detail = detail


class GitLocator:
    """A ledger addressed as a ref in a git repository: `git+<repository>#<ref>`.

    `git+<repository>#<ref>:<namespace>` addresses a ledger kept in a subtree
    of that ref. Two ledgers can then share one ref -- one commit history, one
    compare-and-swap, one push -- without their keys colliding: each store
    sees only its own subtree. The repository ledger of this organization's
    `.github` lives this way inside the designated organization ledger ref,
    so its chain is as durable as the organization's and no second ref is
    named. A namespace is a key prefix, not a ref: `:` cannot appear in a
    ref name, so the two halves never mix.
    """

    def __init__(self, repository, ref, namespace=None):
        self.repository = Path(repository)
        self.ref = ref
        self.namespace = namespace

    def __str__(self):
        text = GIT_SCHEME + str(self.repository) + "#" + self.ref
        return text + ":" + self.namespace if self.namespace else text

    def __repr__(self):
        return "GitLocator(" + repr(str(self)) + ")"

    def __eq__(self, other):
        return isinstance(other, GitLocator) and str(self) == str(other)

    def __hash__(self):
        return hash(str(self))


def _require_ref(ref, locator):
    if not ref.startswith("refs/"):
        raise LedgerLocatorInvalid(locator, "ref_must_be_fully_qualified_under_refs")
    checked = subprocess.run(["git", "check-ref-format", ref], capture_output=True)
    if checked.returncode != 0:
        raise LedgerLocatorInvalid(locator, "ref_is_not_a_valid_git_ref")
    return ref


# The keys a ledger writes at the top of its tree. A namespace may not begin
# with one of them, or a namespaced store would write into another store's
# documents instead of beside them.
_TOP_LEVEL_KEYS = ("HEAD.json", RECEIPT_PREFIX.rstrip("/"), SOURCE_PREFIX.rstrip("/"))


def _require_namespace(namespace, locator):
    """A namespace is a relative, normalized tree path with no reserved head."""
    segments = namespace.split("/")
    # `.github` is a repository name, so a leading dot is allowed; only the
    # path-relative segments and separators that could escape the subtree are not.
    if any(segment in ("", ".", "..") or
           any(c.isspace() or c in ":\\" for c in segment) for segment in segments):
        raise LedgerLocatorInvalid(locator, "namespace_is_not_a_relative_normalized_tree_path")
    if segments[0] in _TOP_LEVEL_KEYS:
        raise LedgerLocatorInvalid(locator, "namespace_may_not_shadow_a_ledger_key")
    return namespace


def parse_locator(value):
    """The store location a supplied value names.

    A `git+<repository>#<ref>` string (or a locator printed from one) names a
    git ref; both halves are required and the ref must be fully qualified, so
    nothing about where a chain lives is filled in here. An optional
    `:<namespace>` after the ref names a subtree of that ref (see
    `GitLocator`). Anything else is a filesystem path, resolved exactly as the
    ledger roots always resolved it.
    """
    if not isinstance(value, PurePath):
        text = str(value)
        if text.startswith(GIT_SCHEME):
            repository, separator, ref = text[len(GIT_SCHEME):].rpartition("#")
            if not separator or not repository or not ref:
                raise LedgerLocatorInvalid(text, "git_locator_requires_repository_and_ref")
            ref, has_namespace, namespace = ref.partition(":")
            if has_namespace and not namespace:
                raise LedgerLocatorInvalid(text, "namespace_separator_without_a_namespace")
            return GitLocator(Path(repository).expanduser().resolve(), _require_ref(ref, text),
                              _require_namespace(namespace, text) if has_namespace else None)
    return Path(value).expanduser().resolve()


def open_store(location):
    """The store at a supplied location: a git ref for a git locator, else POSIX.

    A POSIX root is opened as given, exactly as callers opened it before, so
    an existing ledger root and the locators it publishes are unchanged.
    """
    if not isinstance(location, PurePath) and str(location).startswith(GIT_SCHEME):
        locator = parse_locator(location)
        return GitLedgerStore(locator.repository, locator.ref, locator.namespace)
    return PosixLedgerStore(location)


class PosixLedgerStore:
    """A ledger store on one POSIX filesystem.

    Durability is same-directory atomic replacement plus an fsync of the file
    and its directory. Appenders are serialized by an advisory lock, which
    holds only within one kernel -- two nodes on separate filesystems would
    each take "the lock" and both append. A networked store implements
    `exclusive` as a no-op and serializes through `compare_and_swap` instead.
    """

    kind = "POSIX_FILESYSTEM"

    def __init__(self, root):
        self.root = Path(root)
        self._lock_path = self.root / ".append.lock"
        self._held = 0

    def _path(self, key):
        return self.root / key

    def initialize(self):
        (self.root / RECEIPT_PREFIX.rstrip("/")).mkdir(parents=True, exist_ok=True)

    def locator(self, key):
        """How this substrate names the key, for a reader outside the ledger."""
        return str(self._path(key))

    def exists(self, key):
        return self._path(key).is_file()

    def get(self, key):
        path = self._path(key)
        if not path.is_file():
            return None
        return json.loads(path.read_text())

    def put(self, key, value):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".org-append-", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(_document_bytes(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            self._sync_directory(path.parent)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def list_prefix(self, prefix):
        directory = self._path(prefix.rstrip("/"))
        if not directory.is_dir():
            return set()
        return {prefix + item.name for item in directory.glob("*.json")}

    @contextmanager
    def exclusive(self):
        """Serialize appenders, re-entrantly within one store instance.

        `flock` is held per open file description, so a second acquisition
        from the same process would block on the first forever. Callers nest
        legitimately -- compare_and_swap serializes internally and may be
        invoked from inside an append -- so re-entry is counted rather than
        re-locked.
        """
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

    def compare_and_swap(self, key, expected, value):
        """Publish `value` at `key` only if it still holds `expected`.

        Serialization a networked store gets natively. Here it is the advisory
        lock again, so the guarantee is the same one `exclusive` gives and no
        stronger.
        """
        with self.exclusive():
            if self.get(key) != expected:
                return False
            self.put(key, value)
            return True


    def append_transaction(self, receipt_key_name, receipt, expected_head, new_head, immutable=None):
        """Atomically publish a receipt and HEAD if HEAD still equals expected_head.

        This is the substrate portability contract. A lost comparison writes
        nothing, so a competing writer cannot leave an orphan receipt. A
        network/KV sibling implements this with its native transaction/CAS;
        POSIX uses its local lock only inside the storage primitive.

        `immutable` maps further content-addressed keys (the exact source
        transition receipt under `source-receipts/`) to their documents. They
        are inside the same boundary: every collision is checked before any
        write, so a refused or lost append writes none of them, and HEAD is
        written last, so a document is published only once HEAD names its
        receipt.
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

    @staticmethod
    def _sync_directory(directory):
        fd = os.open(str(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class GitLedgerStore:
    """A ledger store on one ref of a git repository.

    The ledger's documents are blobs in the tree the ref names, at the same
    keys a POSIX root uses as paths. Every write is one commit whose parent is
    the commit it was computed from, published with `git update-ref <ref>
    <new> <old>`: git's own atomic compare-and-swap on the ref. A writer whose
    view went stale loses that comparison and publishes nothing; the objects
    it wrote are unreachable and the chain is untouched. A transaction's
    receipt, retained source receipts and HEAD land in one commit, so no
    reader ever sees a receipt HEAD does not name or a HEAD whose receipt is
    absent.

    `exclusive` is the same re-entrant advisory lock the POSIX store takes,
    held in the repository's git directory, so appenders on one machine queue
    rather than spin. It is not what makes an append safe: the ref
    comparison is, and it holds for any writer that reaches the repository,
    lock or no lock.

    Receipts are immutable here by construction, not only by convention: a
    write that would replace a document under `receipts/` or
    `source-receipts/` with different content is refused as a collision, and
    the ref's history keeps every version that was ever published.

    Only the stdlib and the `git` command are used. Opening a store names no
    repository or ref of its own; the location is always supplied.
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

    def __init__(self, repository, ref, namespace=None):
        self.repository = Path(repository)
        text = GIT_SCHEME + str(repository) + "#" + str(ref) + (":" + str(namespace) if namespace else "")
        self.ref = _require_ref(ref, text)
        # The subtree of the ref this store reads and writes; None is the whole
        # tree. Keys are translated at the git boundary only, so every reader
        # and writer above sees the same `HEAD.json` and `receipts/` it always did.
        self.namespace = _require_namespace(namespace, text) if namespace else None
        self._local = threading.local()

    def _path(self, key):
        return self.namespace + "/" + key if self.namespace else key

    # -- git plumbing -----------------------------------------------------

    def _git_dir(self):
        """The repository's git directory, or None while it does not exist.

        Named explicitly rather than discovered, so a missing repository is
        never mistaken for whatever repository happens to enclose it.
        """
        if (self.repository / "HEAD").is_file() and (self.repository / "objects").is_dir():
            return self.repository
        dotgit = self.repository / ".git"
        if dotgit.is_dir():
            return dotgit
        if dotgit.is_file():
            # A linked worktree or submodule: `.git` names the real directory.
            pointer = dotgit.read_text().strip()
            if pointer.startswith("gitdir:"):
                return (self.repository / pointer[len("gitdir:"):].strip()).resolve()
        return None

    def _env(self, git_dir, **extra):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(self._IDENTITY)
        env["GIT_DIR"] = str(git_dir)
        env.update(extra)
        return env

    def _git(self, *args, stdin=None, env=None, check=True):
        git_dir = self._git_dir()
        if git_dir is None:
            raise FileNotFoundError("ledger repository not initialized: " + str(self.repository))
        completed = subprocess.run(["git", *args], input=stdin, capture_output=True,
                                   env=env or self._env(git_dir))
        if check and completed.returncode != 0:
            raise RuntimeError("git " + args[0] + " failed: " + completed.stderr.decode(errors="replace").strip())
        return completed

    def _tip(self):
        """The commit the ref names now, or None for a ledger never written."""
        if self._git_dir() is None:
            return None
        completed = self._git("rev-parse", "--verify", "--quiet", self.ref + "^{commit}", check=False)
        if completed.returncode != 0:
            if self._git("show-ref", "--verify", "--quiet", self.ref, check=False).returncode == 0:
                raise ValueError("ledger_ref_does_not_name_a_commit: " + self.ref)
            return None
        return completed.stdout.decode().strip()

    def _read(self, revision, keys):
        """The raw documents `keys` name in `revision`, None where absent."""
        keys = list(keys)
        if revision is None or not keys:
            return {key: None for key in keys}
        request = "".join(revision + ":" + self._path(key) + "\n" for key in keys).encode()
        output = self._git("cat-file", "--batch", stdin=request).stdout
        found, offset = {}, 0
        for key in keys:
            end = output.index(b"\n", offset)
            header = output[offset:end].decode().split(" ")
            offset = end + 1
            if header[-1] == "missing" or header[-1] == "ambiguous":
                found[key] = None
                continue
            object_type, size = header[1], int(header[2])
            body = output[offset:offset + size]
            offset += size + 1
            if object_type != "blob":
                raise ValueError("ledger_key_is_not_a_document: " + key)
            found[key] = body
        return found

    def _load(self, revision, keys):
        return {key: None if raw is None else json.loads(raw)
                for key, raw in self._read(revision, keys).items()}

    def _commit(self, parent, documents, message):
        """A commit of `parent`'s tree with `documents` written, or `parent` if unchanged."""
        git_dir = self._git_dir()
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

    def _publish(self, new, expected):
        """Move the ref from `expected` to `new` atomically; False if it moved first."""
        old = expected if expected is not None else "0" * len(new)
        completed = self._git("update-ref", "-m", "ledger append", self.ref, new, old, check=False)
        if completed.returncode == 0:
            return True
        stderr = completed.stderr.decode(errors="replace")
        if self._tip() != expected or "File exists" in stderr:
            return False
        raise RuntimeError("git update-ref failed: " + stderr.strip())

    def _refuse_replacing_immutable(self, current, key, encoded):
        if key.startswith(self._IMMUTABLE_PREFIXES) and current is not None and current != encoded:
            raise ValueError("ledger_receipt_collision")

    # -- the store contract -----------------------------------------------

    def initialize(self):
        """Open the repository, creating an empty bare one at a supplied path that has none.

        The ref itself is not created: a ledger nothing was appended to has
        no HEAD, exactly as an empty POSIX root has none.
        """
        if self._git_dir() is not None:
            return
        if self.repository.exists() and any(self.repository.iterdir()):
            if self._git_dir() is not None:
                return  # a concurrent first writer renamed its repository into place
            raise LedgerLocatorInvalid(str(self.repository), "location_is_not_a_git_repository")
        # Initialized beside the location and renamed into place, so concurrent
        # first writers race on one atomic rename rather than on a half-made
        # repository; the loser discards its copy and opens the winner's.
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
                shutil.rmtree(staging)

    def locator(self, key):
        """How this substrate names the key, for a reader outside the ledger."""
        return GIT_SCHEME + str(self.repository) + "#" + self.ref + ":" + self._path(key)

    def exists(self, key):
        return self._read(self._tip(), [key])[key] is not None

    def get(self, key):
        return self._load(self._tip(), [key])[key]

    def put(self, key, value):
        encoded = _document_bytes(value)
        for _attempt in range(self._ATTEMPTS):
            tip = self._tip()
            self._refuse_replacing_immutable(self._read(tip, [key])[key], key, encoded)
            self.initialize()
            new = self._commit(tip, {key: encoded}, "ledger put " + key)
            if new == tip or self._publish(new, tip):
                return
        raise RuntimeError("LEDGER_STORE_PUT_CONTENTION_EXHAUSTED")

    def list_prefix(self, prefix):
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
    def exclusive(self):
        """Serialize appenders on this machine, re-entrantly per thread.

        The lock file lives in the repository's git directory, one per ref.
        Re-entry is counted per thread, so threads sharing one store each take
        the lock rather than one thread's hold admitting another.
        """
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
        with (self._git_dir() / name).open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self._local.held = 1
            try:
                yield self
            finally:
                self._local.held = 0

    def compare_and_swap(self, key, expected, value):
        """Publish `value` at `key` only if it still holds `expected`.

        The comparison is made against the commit the new one is built on, and
        the ref only moves if it still names that commit; a ref moved by a
        write elsewhere is re-read and compared again.
        """
        encoded = _document_bytes(value)
        with self.exclusive():
            for _attempt in range(self._ATTEMPTS):
                tip = self._tip()
                raw = self._read(tip, [key])[key]
                if (None if raw is None else json.loads(raw)) != expected:
                    return False
                self._refuse_replacing_immutable(raw, key, encoded)
                new = self._commit(tip, {key: encoded}, "ledger compare-and-swap " + key)
                if new == tip or self._publish(new, tip):
                    return True
        raise RuntimeError("LEDGER_STORE_CAS_CONTENTION_EXHAUSTED")

    def append_transaction(self, receipt_key_name, receipt, expected_head, new_head, immutable=None):
        """Atomically publish a receipt and HEAD if HEAD still equals expected_head.

        The same contract as `PosixLedgerStore.append_transaction`, carried by
        one commit and one ref compare-and-swap: a lost comparison returns
        False and publishes nothing; a collision with a different document
        already at a content-addressed key raises `ledger_receipt_collision`
        before anything is written. A ref moved by a write that left HEAD as
        expected is re-read and the transaction rebuilt on it.
        """
        documents = dict(immutable or {})
        documents[receipt_key_name] = receipt
        with self.exclusive():
            for _attempt in range(self._ATTEMPTS):
                tip = self._tip()
                current = self._read(tip, [HEAD_KEY, *documents])
                head = current.pop(HEAD_KEY)
                if (None if head is None else json.loads(head)) != expected_head:
                    return False
                writes = {}
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
        raise RuntimeError("LEDGER_STORE_APPEND_CONTENTION_EXHAUSTED")


# Callers that already name the POSIX store keep that name. A git locator
# still opens GitLedgerStore through open_store.
Store = PosixLedgerStore
