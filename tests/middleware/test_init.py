from __future__ import annotations

import kidsbench.middleware as mw


def test_middleware_exports() -> None:
    assert "track_metrics" in mw.__all__
    assert hasattr(mw, "SidecarStore")
    assert hasattr(mw, "VirtualClock")
    assert hasattr(mw, "FallbackChain")
