"""Public middleware APIs for KidsBench adapters."""
from .embedding import BgeM3Local, CachedEmbedding, EmbeddingService, GeminiEmbedding
from .errors import (
    AuthError,
    LogicError,
    NetworkError,
    QuotaExceededError,
    RateLimitError,
    TimeoutError_,
    wrap_errors,
)
from .fallback import (
    ComputedFallback,
    DeclaredFallback,
    FallbackStrategy,
    SimulatedFallback,
    WrappedFallback,
)
from .llm_client import FallbackChain, LLMClient, LLMResponse, QwenMaxClient
from .metrics import METRICS, MetricsCollector, metrics_context, set_metrics_context, track_metrics
from .nli_judge import NLIJudge, NLIResult, judge_facts_nli
from .noise_injector import inject
from .observe import StructuredLogger
from .preflight import PreflightChecker, PreflightResult, check_cpu_avx2
from .rate_limiter import GlobalRateLimiter, TokenBucketLimiter
from .sidecar import SidecarStore
from .virtual_clock import VirtualClock, get_clock

__all__ = [
    "METRICS",
    "AuthError",
    "BgeM3Local",
    "CachedEmbedding",
    "ComputedFallback",
    "DeclaredFallback",
    "EmbeddingService",
    "FallbackChain",
    "FallbackStrategy",
    "GeminiEmbedding",
    "GlobalRateLimiter",
    "LLMClient",
    "LLMResponse",
    "LogicError",
    "MetricsCollector",
    "NLIJudge",
    "NLIResult",
    "NetworkError",
    "PreflightChecker",
    "PreflightResult",
    "QuotaExceededError",
    "QwenMaxClient",
    "RateLimitError",
    "SidecarStore",
    "SimulatedFallback",
    "StructuredLogger",
    "TimeoutError_",
    "TokenBucketLimiter",
    "VirtualClock",
    "WrappedFallback",
    "check_cpu_avx2",
    "get_clock",
    "inject",
    "judge_facts_nli",
    "metrics_context",
    "set_metrics_context",
    "track_metrics",
    "wrap_errors",
]
