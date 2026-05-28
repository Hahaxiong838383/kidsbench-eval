from __future__ import annotations

import builtins
import io

from kidsbench.contract import Dependency
from kidsbench.middleware.preflight import PreflightChecker, check_cpu_avx2


def test_preflight_checker_runs_all_and_nonfatal(monkeypatch) -> None:
    checker = PreflightChecker()
    monkeypatch.setenv("KB_KEY", "1")

    deps = [
        Dependency("env_ok", "env", check_hint="env KB_KEY"),
        Dependency("env_miss", "env", check_hint="env NO_SUCH"),
        Dependency("swap", "internal_llm", swap_supported=False),
    ]
    out = checker.check(deps)
    assert len(out) == 3
    assert out[0].passed is True
    assert out[1].passed is False
    assert out[2].passed is False


def test_preflight_network_check_with_curl(monkeypatch) -> None:
    checker = PreflightChecker()

    def fake_run(cmd, capture_output, text, timeout, check):
        class Proc:
            returncode = 0
            stdout = "ok"
            stderr = ""

        assert cmd[0] == "curl"
        assert timeout == 5
        return Proc()

    import kidsbench.middleware.preflight as pre_mod

    monkeypatch.setattr(pre_mod.subprocess, "run", fake_run)
    dep = Dependency("qdrant", "service", check_hint="curl http://localhost:6333")
    out = checker.check([dep])[0]
    assert out.passed is True


def test_check_cpu_avx2_linux(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")

    def fake_open(path, encoding=None):
        assert path == "/proc/cpuinfo"
        return io.StringIO("flags : avx2 sse4")

    monkeypatch.setattr(builtins, "open", fake_open)
    assert check_cpu_avx2() is True


def test_check_cpu_avx2_macos(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")

    def fake_run(cmd, capture_output, text, timeout, check):
        class Proc:
            returncode = 0
            stdout = "AVX2 SSE4"
            stderr = ""

        return Proc()

    import kidsbench.middleware.preflight as pre_mod

    monkeypatch.setattr(pre_mod.subprocess, "run", fake_run)
    assert check_cpu_avx2() is True
