from __future__ import annotations

import pytest

from kidsbench.middleware.errors import LogicError, wrap_errors


@wrap_errors({"builtins.ValueError": LogicError})
def _raise_value_error() -> None:
    raise ValueError("bad")


@wrap_errors({"builtins.KeyError": LogicError})
def _raise_type_error() -> None:
    raise TypeError("bad type")


@wrap_errors({"missing.module.Error": LogicError})
def _raise_runtime_error() -> None:
    raise RuntimeError("runtime")


def test_wrap_errors_maps_known_exception() -> None:
    with pytest.raises(LogicError, match="bad"):
        _raise_value_error()


def test_wrap_errors_unmapped_keeps_original() -> None:
    with pytest.raises(TypeError):
        _raise_type_error()


def test_wrap_errors_missing_import_path_does_not_swallow() -> None:
    with pytest.raises(RuntimeError):
        _raise_runtime_error()
