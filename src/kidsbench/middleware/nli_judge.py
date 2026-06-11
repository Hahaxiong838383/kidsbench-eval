"""NLI 蕴含判定器（B 决策：内容验证，judge 独立于被测 LLM）。

判"系统答案是否语义蕴含 hypothesis"。剥离表述差异（"布偶猫" vs "养了布偶猫"），
只测语义信息保留。

判分原则（HARNESS_INTERFACE_SPEC 缺口1）：
- **label 主判**：entailment = pass（不卡 confidence 阈值，LLM 自报置信不可信）
- confidence 仅路由：< 0.7 标记 need_human（进人工抽检池）
- negative（mutually_exclusive）：答案蕴含互斥命题 = 凭常识乱猜 = 扣分

judge 模型独立于被测系统（被测锁 gemini-3-flash → judge 用 Qwen）。温度锁 0。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

_SYSTEM_PROMPT = (
    "你是严谨的逻辑判定器。判断【前提】是否在语义上蕴含【假设】。\n"
    "- entailment：前提的信息明确支持假设（即使表述不同，如『布偶猫』支持『养了布偶猫』）\n"
    "- contradiction：前提与假设矛盾\n"
    "- neutral：前提既不支持也不否定假设（信息不足）\n"
    '只输出 JSON：{"label":"entailment|contradiction|neutral","confidence":0.0-1.0}'
)

# confidence 低于此值 → 路由人工抽检（不影响自动 label 判定）
HUMAN_REVIEW_THRESHOLD = 0.7

CompletionFn = Callable[[list[dict]], str]


@dataclass(frozen=True)
class NLIResult:
    label: str
    confidence: float

    @property
    def is_entailment(self) -> bool:
        return self.label == "entailment"

    @property
    def low_confidence(self) -> bool:
        return self.confidence < HUMAN_REVIEW_THRESHOLD


class NLIJudge:
    """调 judge 模型做 NLI 蕴含判定。completion_fn 可注入（测试 mock）。"""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        completion_fn: CompletionFn | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._completion_fn = completion_fn or self._real_completion

    @classmethod
    def from_preset(cls, preset_name: str = "qwen-judge", **kwargs) -> NLIJudge:
        """从 LLM preset 构造（key 从 env 注入，永不硬编码）。"""
        from kidsbench.config import load_preset

        p = load_preset(preset_name)
        return cls(model=p.model, base_url=p.base_url, api_key=p.get_api_key(), **kwargs)

    def entail(self, premise: str, hypothesis: str) -> NLIResult:
        """判 premise 是否蕴含 hypothesis。"""
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"【前提】{premise}\n【假设】{hypothesis}"},
        ]
        content = self._completion_fn(messages)
        return self._parse(content)

    def _real_completion(self, messages: list[dict]) -> str:
        import httpx

        body = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 256,
            "response_format": {"type": "json_object"},
            "enable_thinking": False,
        }
        from .retry import retry_call

        def _post() -> dict:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                )
                resp.raise_for_status()
                return resp.json()

        data = retry_call(_post, max_attempts=3, base_delay=1.0)  # 网络抖动/5xx 重试
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"NLI judge 无 choices: {data}")
        return choices[0].get("message", {}).get("content") or ""

    @staticmethod
    def _parse(content: str) -> NLIResult:
        """解析 judge JSON。失败 → neutral/0.0（保守，进人工）。"""
        try:
            obj = json.loads(content)
            label = str(obj.get("label", "neutral")).lower().strip()
            if label not in ("entailment", "contradiction", "neutral"):
                label = "neutral"
            conf = float(obj.get("confidence", 0.0))
        except (json.JSONDecodeError, ValueError, TypeError):
            return NLIResult(label="neutral", confidence=0.0)
        return NLIResult(label=label, confidence=max(0.0, min(1.0, conf)))


def judge_facts_nli(
    answer: str,
    expected_facts: list[dict],
    negative_facts: list[dict],
    nli: NLIJudge,
) -> dict:
    """NLI 内容验证判分（替代 regex_judge 的语义版）。

    - positive：答案需蕴含全部 expected_facts.hypothesis
    - negative：答案蕴含任一 mutually_exclusive 命题 = 乱猜 → wrong
    - confidence<0.7 → need_human=True（人工抽检）

    返回 verdict（correct/wrong/evasive）+ score + need_human + 明细。
    """
    pos = [(f["hypothesis"], nli.entail(answer, f["hypothesis"])) for f in expected_facts]
    neg = [(f["hypothesis"], nli.entail(answer, f["hypothesis"])) for f in negative_facts]

    positive_pass = bool(pos) and all(r.is_entailment for _, r in pos)
    guessed = any(r.is_entailment for _, r in neg)
    need_human = any(r.low_confidence for _, r in pos + neg)

    if guessed:
        verdict = "wrong"  # 蕴含互斥命题 = 凭常识乱猜（最危险）
    elif positive_pass:
        verdict = "correct"
    else:
        verdict = "evasive"  # 没说对也没乱猜

    # score = positive 命中比例（partial credit，2026-06-11 新题库多命题口径）：
    # 旧题多为单命题（比例 ∈ {0,1}，与原 0/1 二值完全一致，零回归）；
    # 新题 2-3 命题，「3 中 2」给 0.67 而非一刀切 0——区分度更平滑。
    # verdict 三值判定不变（correct 仍=全蕴含），acc 统计口径不受影响；
    # 蕴含互斥命题（乱猜）仍直接 0 分。
    if guessed:
        score = 0.0
    elif pos:
        score = sum(1 for _, r in pos if r.is_entailment) / len(pos)
    else:
        score = 0.0
    return {
        "verdict": verdict,
        "score": score,
        "need_human": need_human,
        "positive_pass": positive_pass,
        "guessed": guessed,
        "positive_hits": [h for h, r in pos if r.is_entailment],
        "negative_hits": [h for h, r in neg if r.is_entailment],
    }
