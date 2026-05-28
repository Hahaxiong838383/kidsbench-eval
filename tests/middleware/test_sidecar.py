from __future__ import annotations

import threading

from kidsbench.middleware.sidecar import SidecarStore


def _assert_many_to_many(store: SidecarStore) -> None:
    store.put("u1", "t1", ["m1", "m2"])
    store.put("u1", "t2", ["m2", "m3"])

    assert store.get_memory_ids("u1", "t1") == ["m1", "m2"]
    assert store.get_turn_ids("u1", "m2") == ["t1", "t2"]

    stats = store.stats("u1")
    assert stats["turn_count"] == 2
    assert stats["memory_count"] == 3
    assert stats["mapping_count"] == 4


def test_sidecar_memory_backend_many_to_many_and_clear() -> None:
    store = SidecarStore(backend="memory")
    _assert_many_to_many(store)
    deleted = store.clear_user("u1")
    assert deleted == 4
    assert store.get_memory_ids("u1", "t1") == []


def test_sidecar_sqlite_backend_persistence(tmp_path) -> None:
    path = tmp_path / "sidecar.sqlite3"
    store = SidecarStore(backend="sqlite", path=path)
    _assert_many_to_many(store)

    another = SidecarStore(backend="sqlite", path=path)
    assert another.get_turn_ids("u1", "m2") == ["t1", "t2"]


def test_sidecar_concurrent_puts() -> None:
    store = SidecarStore(backend="memory")

    def worker(i: int) -> None:
        store.put("u2", f"t{i}", ["m_shared"])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert len(store.get_turn_ids("u2", "m_shared")) == 20
