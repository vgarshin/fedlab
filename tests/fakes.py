from __future__ import annotations

import json
from types import SimpleNamespace

from core.schemas import CRITERIA


def good_grade(score: int = 7, feedback: str = "Хорошая работа с понятной логикой.", sum_wrong: bool = False) -> dict:
    scores = {k: {"name": n, "score": score, "evidence": [{"slide": 1, "description": "факт"}], "feedback": feedback}
              for k, n in CRITERIA.items()}
    total = score * 6
    return {"team_name": "T", "status": "EVALUATED", "disqualified": False, "disqualification_code": None,
            "disqualification_reason": None, "disqualification_evidence": [], "scores": scores,
            "sum_score_60": total + (3 if sum_wrong else 0), "total_score_100": 1 if sum_wrong else round(total / 60 * 100),
            "general_feedback": feedback, "technical_warnings": [], "needs_human_review": False}


def good_detect(p: float = 0.2) -> dict:
    return {"ai_probability": p, "verdict": "low", "confidence": "medium",
            "signals": {"generic_phrasing": {"score": 0.1, "evidence": "слайд 1"}}, "rationale": "Текст живой."}


class FakeClient:
    """Stands in for AsyncOpenAI. ``replies`` are consumed in order (str or dict -> JSON)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0) if self.replies else self.calls and self._last
        self._last = r
        text = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200),
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
