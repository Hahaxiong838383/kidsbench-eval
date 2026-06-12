#!/usr/bin/env bash
# MemMachine server 本地部署（全 SQLite 嵌入式，Phase 0 实测，2026-06-12）。
#
# 部署形态（最大疑虑解除）：docker-compose 默认 PG+Neo4j 只是推荐形态；
# 全 SQLite 路径实测可跑——episodic=event backend + sqlite_vector_store(usearch)
# + sqlite 关系库，零 docker / 零 Postgres / 零 Neo4j。
#
# 依赖本地 embedding shim 在 18230（bge-small-zh-v1.5，512d）。先起 shim：
#   .venv/bin/python -m kidsbench.middleware.embedding_shim --port 18230
#
# 用法: bash scripts/setup_memmachine_server.sh   （在 kidsbench-eval 根目录）
# 产出: MemMachine server（API base http://127.0.0.1:8021/api/v2）

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
VENV="$ROOT/.venv-memmachine"
WORKDIR=/tmp/kb-phase0-memmachine
PORT=8021

if [ ! -x "$VENV/bin/memmachine-server" ]; then
  echo "❌ $VENV 不存在或未装 memmachine。先："
  echo "   python3.12 -m venv .venv-memmachine && .venv-memmachine/bin/pip install \\"
  echo "     -e /tmp/kb-survey/memmachine/packages/common -e .../packages/server -e .../packages/client requests"
  exit 1
fi

# 已在跑就跳过
if curl -s -m 3 "http://127.0.0.1:$PORT/api/v2/health" >/dev/null 2>&1; then
  echo "✅ MemMachine 已在 $PORT 运行，跳过"
  exit 0
fi

echo "▶ [1/3] 检查 embedding shim（18230）"
if ! curl -s -m 3 http://127.0.0.1:18230/v1/embeddings \
     -H "Content-Type: application/json" \
     -d '{"model":"BAAI/bge-small-zh-v1.5","input":["x"]}' >/dev/null 2>&1; then
  echo "⚠️  shim 18230 未响应。先跑：.venv/bin/python -m kidsbench.middleware.embedding_shim --port 18230"
  exit 1
fi

echo "▶ [2/3] 写 cfg.yml（全 SQLite，key 脱敏）"
mkdir -p "$WORKDIR"
DSK=$(grep '^KIDSBENCH_DEEPSEEK_API_KEY=' "$ROOT/.env.local" | cut -d= -f2)
cat > "$WORKDIR/cfg.yml" <<EOF
logging: {path: $WORKDIR/mem-machine.log, level: info}
server: {host: 127.0.0.1, port: $PORT}
episode_store: {database: profile_storage}
episodic_memory:
  long_term_memory:
    backend: event
    embedder: openai_embedder
    vector_store: event_vector_store
    segment_store: profile_storage
    segmenter: {type: text}
    deriver: {type: sentence_text}
  short_term_memory: {llm_model: openai_model, message_capacity: 500}
retrieval_agent: {llm_model: openai_model}
semantic_memory:
  llm_model: openai_model
  embedding_model: openai_embedder
  database: profile_storage
  config_database: profile_storage
  storage_backend: vector_store
  feature_store: profile_storage
  vector_collection: semantic_vector_store
  vector_dimensions: 512
  vector_similarity_metric: cosine
session_manager: {database: profile_storage}
resources:
  databases:
    profile_storage: {provider: sqlite, config: {path: $WORKDIR/memmachine.db}}
    event_vector_store: {provider: sqlite_vector_store, config: {path: $WORKDIR/event_vectors.db, vector_search_engine: usearch}}
    semantic_vector_store: {provider: sqlite_vector_store, config: {path: $WORKDIR/semantic_vectors.db, vector_search_engine: usearch}}
  embedders:
    openai_embedder: {provider: openai, config: {max_input_length: 2048, model: "BAAI/bge-small-zh-v1.5", api_key: "dummy-local", base_url: "http://127.0.0.1:18230/v1", dimensions: 512}}
  language_models:
    openai_model: {provider: openai-chat-completions, config: {model: "deepseek-v4-flash", api_key: "$DSK", base_url: "https://api.deepseek.com/v1"}}
EOF
chmod 600 "$WORKDIR/cfg.yml"

echo "▶ [3/3] 起 server（后台，启动含 embedder+LLM 双 validate 真调）"
cd "$WORKDIR"
MEMORY_CONFIG="$WORKDIR/cfg.yml" nohup "$VENV/bin/memmachine-server" \
  > "$WORKDIR/server.log" 2>&1 &
for i in $(seq 1 12); do
  if curl -s -m 3 "http://127.0.0.1:$PORT/api/v2/health" >/dev/null 2>&1; then
    echo "✅ MemMachine 就绪：http://127.0.0.1:$PORT/api/v2（全 SQLite，零外部服务）"
    exit 0
  fi
  sleep 3
done
echo "❌ server 启动超时，看 $WORKDIR/server.log"
exit 1
