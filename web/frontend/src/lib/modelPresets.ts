/**
 * 常见 LLM 模型的 max_tokens 上限映射。
 *
 * 用于：
 * - 表单切换 model 时自动设 input.max 属性
 * - 默认 max_tokens 推荐值
 * - 用户输入未知 model 时给出保守默认
 *
 * 数据源：各家官方文档（截至 2026-05）。模型快速迭代，如有新版以官方为准。
 */

export interface ModelLimits {
  /** max_tokens 上限（output tokens）*/
  max: number;
  /** 推荐默认值 */
  default: number;
  /** provider 提示 */
  hint?: string;
}

export const MODEL_LIMITS: Record<string, ModelLimits> = {
  // Gemini 系列（via GEMINI_PROXY Vertex AI）
  "gemini-3.5-flash":     { max: 65535, default: 4096, hint: "Vertex AI exclusive max" },
  "gemini-3-flash-preview": { max: 8192, default: 4096 },
  "gemini-3.1-flash-lite-preview": { max: 8192, default: 4096 },
  "gemini-3.1-pro-preview": { max: 32768, default: 8192 },
  "gemini-3-pro-preview":  { max: 32768, default: 8192 },
  "gemini-2.5-flash":      { max: 8192, default: 4096 },
  "gemini-2.5-pro":        { max: 65535, default: 8192 },

  // DeepSeek
  "deepseek-chat":         { max: 8192, default: 4096 },
  "deepseek-reasoner":     { max: 65536, default: 8192, hint: "Reasoner 模型，含 CoT" },
  "deepseek-v4-pro":       { max: 32768, default: 4096 },

  // OpenAI GPT
  "gpt-4o":                { max: 16384, default: 4096 },
  "gpt-4o-mini":           { max: 16384, default: 4096 },
  "gpt-4-turbo":           { max: 4096, default: 4096 },
  "gpt-5":                 { max: 16384, default: 4096 },
  "gpt-5.3-codex":         { max: 32768, default: 8192 },
  "gpt-5.4":               { max: 32768, default: 8192 },
  "gpt-5.5":               { max: 32768, default: 8192 },

  // Anthropic Claude (via proxy)
  "claude-3-7-sonnet":     { max: 8192, default: 4096 },
  "claude-opus-4-7":       { max: 32768, default: 4096 },

  // 国内模型
  "kimi-k1-5":             { max: 8192, default: 4096 },
  "kimi-k2":               { max: 16384, default: 4096 },
  "moonshot-v1-128k":      { max: 32768, default: 4096 },
  "qwen-max":              { max: 8192, default: 4096 },
  "qwen-plus":             { max: 8192, default: 4096 },
  "qwen3-32b":             { max: 8192, default: 4096 },
  "glm-4-plus":            { max: 4096, default: 4096 },
  "glm-4.5":               { max: 8192, default: 4096 },
  "MiniMax-M2.7":          { max: 200000, default: 8192, hint: "Pro tier 长上下文" },

  // 本地部署
  "qwen3-30b":             { max: 8192, default: 4096 },
  "ollama-qwen3-32b":      { max: 8192, default: 4096 },
};

/** 保守默认（用户输入未知 model 时用）*/
export const UNKNOWN_MODEL_LIMITS: ModelLimits = {
  max: 8192,
  default: 4096,
  hint: "未识别 model，按保守默认；如需更高请查官方文档",
};

/** 查询 model 对应的 limits（fallback 保守值）*/
export function getModelLimits(model: string): ModelLimits {
  const exact = MODEL_LIMITS[model];
  if (exact) return exact;
  // 模糊匹配：前缀（如 "gpt-4o-2024-08-06" → "gpt-4o"）
  for (const [key, v] of Object.entries(MODEL_LIMITS)) {
    if (model.startsWith(key)) return v;
  }
  return UNKNOWN_MODEL_LIMITS;
}

/** 所有 model 名（datalist 用）*/
export function listModelNames(): string[] {
  return Object.keys(MODEL_LIMITS).sort();
}
