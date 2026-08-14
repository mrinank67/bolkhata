"""In-memory Firestore double.

Deliberately NOT a general Firestore emulator. It implements only the access
patterns this codebase actually uses, so it stays small enough to trust:

    db.collection(c).document(d).collection(c2).document(d2)
    ref.get() / .set(data, merge=) / .update(data) / .delete()
    collection.stream() / .order_by(f, direction=) / .limit(n) / .where(filter=)
    collection.add(data) / .document()          (auto-id)
    db.transaction()                            (see note below)

Transactions: Firestore's real ``transactional`` decorator drives a live
backend session, so tests that exercise rate_limiter monkeypatch
``rate_limiter._firestore_transactional`` to a pass-through and hand the
callable a :class:`FakeTransaction`. See tests/test_rate_limiter.py.
"""

from __future__ import annotations

import itertools
import re

from google.cloud.firestore_v1 import Increment

# Sentinel object identity for firestore.SERVER_TIMESTAMP is checked by value in
# the app, so we just store whatever sentinel it passes and let assertions look
# for the key rather than the value.

_id_counter = itertools.count(1)


def _auto_id() -> str:
    return f"auto{next(_id_counter):06d}"


def _apply_sentinels(existing: dict, incoming: dict) -> dict:
    """Resolve Increment sentinels against the current value."""
    out = {}
    for key, value in incoming.items():
        if isinstance(value, Increment):
            out[key] = (existing.get(key) or 0) + value.value
        else:
            out[key] = value
    return out


class FakeSnapshot:
    def __init__(self, doc_id: str, data: dict | None, reference: FakeDocumentRef):
        self.id = doc_id
        self._data = data
        self.reference = reference

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict | None:
        return dict(self._data) if self._data is not None else None

    def get(self, field: str):
        return (self._data or {}).get(field)


class FakeDocumentRef:
    def __init__(self, store: FakeFirestore, path: tuple[str, ...]):
        self._store = store
        self._path = path

    @property
    def id(self) -> str:
        return self._path[-1]

    @property
    def path(self) -> str:
        return "/".join(self._path)

    @property
    def parent(self) -> FakeCollectionRef:
        """The collection containing this document."""
        return FakeCollectionRef(self._store, self._path[:-1])

    def collection(self, name: str) -> FakeCollectionRef:
        return FakeCollectionRef(self._store, (*self._path, name))

    # `transaction=` is accepted and ignored: FakeTransaction applies writes
    # immediately, so reads inside one see the same state a real transaction
    # would have read at its snapshot point.
    def get(self, transaction=None) -> FakeSnapshot:
        return FakeSnapshot(self.id, self._store.docs.get(self.path), self)

    def set(self, data: dict, merge: bool = False) -> None:
        if merge:
            existing = self._store.docs.get(self.path) or {}
            merged = dict(existing)
            merged.update(_apply_sentinels(existing, data))
            self._store.docs[self.path] = merged
        else:
            self._store.docs[self.path] = _apply_sentinels({}, data)

    def update(self, data: dict) -> None:
        existing = self._store.docs.get(self.path)
        if existing is None:
            raise KeyError(f"cannot update missing document: {self.path}")
        existing.update(_apply_sentinels(existing, data))

    def delete(self) -> None:
        self._store.docs.pop(self.path, None)
        # Cascade: deleting a doc drops its subcollections too. Real Firestore
        # does NOT do this, but every delete in this app is followed by explicit
        # subcollection cleanup, so cascading keeps the fake's state honest
        # without hiding a missing-cleanup bug at the call sites we assert on.
        prefix = self.path + "/"
        for key in [k for k in self._store.docs if k.startswith(prefix)]:
            self._store.docs.pop(key, None)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FakeDocumentRef {self.path}>"


class _Query:
    """Chainable query. Filters/order/limit are applied lazily in stream()."""

    def __init__(self, collection: FakeCollectionRef):
        self._collection = collection
        self._filters: list[tuple[str, str, object]] = []
        self._order: tuple[str, str] | None = None
        self._limit: int | None = None

    def _clone(self) -> _Query:
        q = _Query(self._collection)
        q._filters = list(self._filters)
        q._order = self._order
        q._limit = self._limit
        return q

    def where(self, field=None, op=None, value=None, *, filter=None) -> _Query:
        q = self._clone()
        if filter is not None:
            q._filters.append((filter.field_path, filter.op_string, filter.value))
        else:
            q._filters.append((field, op, value))
        return q

    def order_by(self, field: str, direction: str = "ASCENDING") -> _Query:
        q = self._clone()
        q._order = (field, str(direction))
        return q

    def limit(self, n: int) -> _Query:
        q = self._clone()
        q._limit = n
        return q

    def stream(self):
        docs = self._collection._raw_docs()

        for field, op, value in self._filters:
            docs = [d for d in docs if _matches(d[1].get(field), op, value)]

        if self._order:
            field, direction = self._order
            reverse = "DESCEND" in direction.upper()
            docs.sort(key=lambda d: _sort_key(d[1].get(field)), reverse=reverse)

        if self._limit is not None:
            docs = docs[: self._limit]

        for doc_id, data in docs:
            ref = self._collection.document(doc_id)
            yield FakeSnapshot(doc_id, dict(data), ref)


def _matches(actual, op: str, expected) -> bool:
    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op == ">":
        return actual is not None and actual > expected
    if op == ">=":
        return actual is not None and actual >= expected
    if op == "<":
        return actual is not None and actual < expected
    if op == "<=":
        return actual is not None and actual <= expected
    if op == "in":
        return actual in expected
    if op == "array_contains":
        return expected in (actual or [])
    raise NotImplementedError(f"FakeFirestore does not implement operator {op!r}")


def _sort_key(value):
    """Order None last-ish and keep mixed types from raising TypeError."""
    if value is None:
        return (0, 0)
    if isinstance(value, (int, float)):
        return (1, value)
    return (2, str(value))


class FakeCollectionRef:
    def __init__(self, store: FakeFirestore, path: tuple[str, ...]):
        self._store = store
        self._path = path

    @property
    def path(self) -> str:
        return "/".join(self._path)

    @property
    def parent(self) -> FakeDocumentRef | None:
        """The document containing this subcollection; None for a root collection.

        routes/orders.py relies on this (``orders_ref.parent``) to reach the
        user document from a subcollection reference.
        """
        if len(self._path) < 2:
            return None
        return FakeDocumentRef(self._store, self._path[:-1])

    def document(self, doc_id: str | None = None) -> FakeDocumentRef:
        return FakeDocumentRef(self._store, (*self._path, doc_id or _auto_id()))

    def add(self, data: dict) -> tuple[None, FakeDocumentRef]:
        ref = self.document()
        ref.set(data)
        return None, ref

    def _raw_docs(self) -> list[tuple[str, dict]]:
        """Direct children only — not documents in nested subcollections."""
        prefix = self.path + "/"
        out = []
        for key, data in self._store.docs.items():
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            if "/" in rest:
                continue
            out.append((rest, data))
        out.sort(key=lambda d: d[0])
        return out

    def stream(self):
        return _Query(self).stream()

    def where(self, field=None, op=None, value=None, *, filter=None):
        return _Query(self).where(field, op, value, filter=filter)

    def order_by(self, field: str, direction: str = "ASCENDING"):
        return _Query(self).order_by(field, direction)

    def limit(self, n: int):
        return _Query(self).limit(n)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FakeCollectionRef {self.path}>"


class FakeTransaction:
    """Applies writes immediately.

    Real Firestore buffers writes until commit; this fake does not, because no
    code path under test reads back a value it wrote earlier in the same
    transaction. If one ever does, this needs a real write buffer.
    """

    def set(self, doc_ref: FakeDocumentRef, data: dict, merge: bool = False) -> None:
        doc_ref.set(data, merge=merge)

    def update(self, doc_ref: FakeDocumentRef, data: dict) -> None:
        doc_ref.update(data)

    def delete(self, doc_ref: FakeDocumentRef) -> None:
        doc_ref.delete()


class FakeFirestore:
    """Root client. `docs` maps a slash-joined path -> dict."""

    def __init__(self, docs: dict[str, dict] | None = None):
        self.docs: dict[str, dict] = dict(docs or {})

    def collection(self, name: str) -> FakeCollectionRef:
        return FakeCollectionRef(self, (name,))

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    # --- test helpers -------------------------------------------------

    def seed(self, path: str, data: dict) -> FakeDocumentRef:
        """Write a document at a full slash path, e.g. 'users/u1/stock/rice'."""
        self.docs[path] = dict(data)
        return FakeDocumentRef(self, tuple(path.split("/")))

    def reset(self) -> None:
        self.docs.clear()

    def paths_under(self, prefix: str) -> list[str]:
        pattern = re.compile("^" + re.escape(prefix.rstrip("/")) + "/")
        return sorted(k for k in self.docs if pattern.match(k))
