"""本地 Embedding Shim：把 sentence-transformers BAAI/bge-small-zh-v1.5 包装成 OpenAI /v1/embeddings 兼容端点。

为什么存在：
- 评测标准要求所有被测记忆系统统一使用本地 BAAI/bge-small-zh-v1.5（512 维，normalize_embeddings=True）。
- 部分系统（如 ReMe）仅支持 OpenAI 兼容的 API 形态 embedding，无法直接加载 HF 模型。
- 此 shim 让 API-only 系统也能对齐评测标准，实现公平可比。

谁在用：
- ReMe（reme-ai）等仅声明 OpenAI embedding endpoint 的 adapter。
- 未来其他仅支持 /v1/embeddings 的记忆/向量后端。

怎么启动（开发/评测用）：
    python -m kidsbench.middleware.embedding_shim --port 18230
    # 或指定 host
    python -m kidsbench.middleware.embedding_shim --host 0.0.0.0 --port 18230

健康检查：
    curl http://127.0.0.1:18230/healthz

OpenAI 客户端用法示例：
    from openai import OpenAI
    client = OpenAI(base_url="http://127.0.0.1:18230/v1", api_key="sk-dummy")
    resp = client.embeddings.create(model="BAAI/bge-small-zh-v1.5", input=["文本1", "文本2"])
    vec = resp.data[0].embedding  # 512 维，L2 norm ≈ 1.0

实现要点：
- 模块级懒加载单例：首请求触发 SentenceTransformer 加载（避免 pytest 收集阶段卡住）。
- encode 固定 normalize_embeddings=True（BGE 官方 cosine 推荐用法）。
- 支持单条 str 和批量 list[str]（后者一次性 batch encode）。
- 空 input（"" 或 []）→ 422；单条 >8192 字符截断并打 warning log。
- usage 粗估：总字符数 // 4（与 OpenAI 风格兼容，不追求精确 token 化）。
- 错误显式抛出，不静默吞（模型加载失败、encode 异常会 500）。
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(
    title="KidsBench Embedding Shim",
    description="OpenAI /v1/embeddings 兼容端点，底层 BAAI/bge-small-zh-v1.5（512d, normalized）",
    version="0.1.0",
)


class EmbeddingsRequest(BaseModel):
    """OpenAI embeddings 请求体（最小兼容）。"""

    model: str
    input: str | list[str]


# 模块级懒加载单例（关键：import 时不触发下载/加载）
_embedding_model: Any | None = None


def _get_embedding_model() -> Any:
    """延迟加载 BGE 模型。首次调用时真正实例化。"""
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers 未安装，无法提供本地 embedding。"
                "请在包含该依赖的环境中运行（.venv 或 embed extra）。"
            ) from exc
        _embedding_model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    return _embedding_model


@app.post("/v1/embeddings")
def create_embeddings(req: EmbeddingsRequest) -> dict[str, Any]:
    """OpenAI 兼容的 embeddings 创建端点。

    - input 支持 str（单条）或 list[str]（批量，一次 encode）。
    - 响应严格包含 object/data/model/usage 字段。
    - 空输入显式 422；超长文本截断 + warning。
    """
    # 空输入校验（"" 或 []）
    if isinstance(req.input, str):
        if not req.input:
            raise HTTPException(status_code=422, detail="input 不能为空字符串")
        texts: list[str] = [req.input]
    else:
        if not req.input:
            raise HTTPException(status_code=422, detail="input 不能为空列表")
        if not all(isinstance(t, str) for t in req.input):
            # Pydantic 已基本保证，但显式防卫
            raise HTTPException(status_code=422, detail="input 列表元素必须均为字符串")
        texts = req.input

    # 超 8192 字符截断（逐条）
    processed: list[str] = []
    for t in texts:
        if len(t) > 8192:
            logger.warning(
                "单条文本超 8192 字符，已截断（len=%d → 8192），model=%s",
                len(t),
                req.model,
            )
            t = t[:8192]
        processed.append(t)
    texts = processed

    # 懒加载 + encode（normalize_embeddings=True 是 bge 标准）
    model = _get_embedding_model()
    try:
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        # encode 返回 ndarray (n, 512)，.tolist() 直接得到 list[list[float]]
        embeddings: list[list[float]] = vecs.tolist()
    except Exception as exc:
        # 显式不吞，FastAPI 会返回 500 + 详情（便于调试）
        logger.exception("embedding encode 失败")
        raise HTTPException(
            status_code=500, detail=f"embedding encode failed: {type(exc).__name__}: {exc}"
        ) from exc

    # 组装 OpenAI 格式响应
    data: list[dict[str, Any]] = []
    for idx, emb in enumerate(embeddings):
        data.append(
            {
                "object": "embedding",
                "embedding": emb,
                "index": idx,
            }
        )

    # usage 粗估：总字符数 // 4（与任务规格一致）
    total_chars = sum(len(t) for t in texts)
    prompt_tokens = total_chars // 4

    return {
        "object": "list",
        "data": data,
        "model": req.model,  # 回显请求的 model 名
        "usage": {
            "prompt_tokens": prompt_tokens,
            "total_tokens": prompt_tokens,
        },
    }


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """健康检查（不触发模型加载）。"""
    return {
        "status": "ok",
        "model": "BAAI/bge-small-zh-v1.5",
        "dim": 512,
    }


def main() -> None:
    """CLI 入口：python -m kidsbench.middleware.embedding_shim --port 18230"""
    parser = argparse.ArgumentParser(
        description="KidsBench 本地 Embedding Shim（OpenAI 兼容 /v1/embeddings）"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听 host（默认 127.0.0.1）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=18230,
        help="监听 port（默认 18230）",
    )
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "kidsbench.middleware.embedding_shim:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
