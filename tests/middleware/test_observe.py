from __future__ import annotations

import json
import threading

from kidsbench.middleware.observe import StructuredLogger


def test_structured_logger_writes_jsonl(tmp_path) -> None:
    logger = StructuredLogger("mem0", run_id="run-x", log_dir=tmp_path)
    logger.info("write_done", user_id="u1", n=1)
    logger.warn("slow_path", ms=12.3)
    logger.error("write_fail", err="boom")

    path = logger.jsonl_path()
    assert path is not None
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3

    first = json.loads(lines[0])
    assert first["adapter"] == "mem0"
    assert first["run_id"] == "run-x"
    assert first["event"] == "write_done"


def test_structured_logger_without_run_id_has_no_file() -> None:
    logger = StructuredLogger("oracle", run_id=None)
    logger.info("hello")
    assert logger.jsonl_path() is None


def test_structured_logger_concurrent_file_writes(tmp_path) -> None:
    logger = StructuredLogger("mem0", run_id="run-y", log_dir=tmp_path)

    def worker(i: int) -> None:
        logger.info("tick", i=i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    path = logger.jsonl_path()
    assert path is not None
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 10
