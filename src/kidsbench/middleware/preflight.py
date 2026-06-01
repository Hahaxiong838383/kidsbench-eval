"""Dependency preflight checks for adapters."""
from __future__ import annotations

import os
import platform
import shlex
import subprocess
from dataclasses import dataclass

from kidsbench.contract import Dependency


@dataclass(frozen=True)
class PreflightResult:
    """Single dependency preflight result."""

    dependency: Dependency
    passed: bool
    detail: str


class PreflightChecker:
    """Run non-fatal checks for adapter dependencies."""

    def check(self, deps: list[Dependency]) -> list[PreflightResult]:
        """Run all checks and return per-dependency results."""
        results: list[PreflightResult] = []
        for dep in deps:
            try:
                passed, detail = self._check_one(dep)
            except Exception as err:
                passed, detail = False, f"check error: {type(err).__name__}: {err}"
            results.append(PreflightResult(dependency=dep, passed=passed, detail=detail))
        return results

    def _check_one(self, dep: Dependency) -> tuple[bool, str]:
        if dep.kind in {"service", "api"}:
            return self._check_network_dep(dep)
        if dep.kind == "env":
            name = _extract_env_name(dep.check_hint)
            if not name:
                return False, "missing env var name in check_hint"
            return bool(os.getenv(name)), f"env {name} {'set' if os.getenv(name) else 'missing'}"
        if dep.kind in {"internal_llm", "internal_embed"}:
            if dep.swap_supported:
                return True, "swap supported"
            return False, "swap unsupported"
        if dep.kind == "model":
            if dep.check_hint:
                return _run_shell_check(dep.check_hint)
            return True, "no model check_hint, skipped"
        return True, "unsupported dependency kind skipped"

    def _check_network_dep(self, dep: Dependency) -> tuple[bool, str]:
        hint = dep.check_hint.strip()
        if not hint:
            return False, "missing network check_hint"
        if hint.startswith("curl "):
            return _run_shell_check(hint)
        return _run_shell_check(f"curl --max-time 5 {shlex.quote(hint)}")


def check_cpu_avx2() -> bool:
    """Detect whether current CPU supports AVX2 instruction set."""
    system = platform.system().lower()
    if system == "linux":
        try:
            text = open("/proc/cpuinfo", encoding="utf-8").read().lower()
        except OSError:
            return False
        return "avx2" in text

    if system == "darwin":
        commands = [
            ["sysctl", "-n", "machdep.cpu.features"],
            ["sysctl", "-n", "machdep.cpu.leaf7_features"],
        ]
        merged = ""
        for cmd in commands:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
            except Exception:
                continue
            merged += " " + proc.stdout.lower()
        return "avx2" in merged

    return False


def _extract_env_name(check_hint: str) -> str:
    parts = check_hint.strip().split()
    if not parts:
        return ""
    if parts[0] == "env" and len(parts) >= 2:
        return parts[1]
    return parts[0]


def _run_shell_check(command: str) -> tuple[bool, str]:
    proc = subprocess.run(
        shlex.split(command),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if proc.returncode == 0:
        return True, proc.stdout.strip() or "ok"
    stderr = proc.stderr.strip() or proc.stdout.strip() or f"exit={proc.returncode}"
    return False, stderr


def verify_unified_injection(
    adapters: dict, expected_llm: str, expected_embed: str
) -> list[dict]:
    """A 决策：校验各 adapter 实际注入的 LLM/embedding 是否统一（公平性命根）。

    各 adapter 经 get_injected_providers() 自报实际 model，与 harness 注入的统一
    model 对比。不一致 → mismatch（harness 应告警或隔离为 Model-Locked 组）；
    baseline / 未实现自报 → unknown（不参与锁定校验）。返回新 list，不改入参。
    """
    results: list[dict] = []
    for name, adapter in adapters.items():
        providers = adapter.get_injected_providers()
        if not providers:
            results.append(
                {"adapter": name, "status": "unknown", "injected_llm": "", "injected_embed": ""}
            )
            continue
        llm = providers.get("internal_llm", "")
        embed = providers.get("internal_embed", "")
        status = "ok" if (llm == expected_llm and embed == expected_embed) else "mismatch"
        results.append(
            {"adapter": name, "status": status, "injected_llm": llm, "injected_embed": embed}
        )
    return results
