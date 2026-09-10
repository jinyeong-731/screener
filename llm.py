"""OpenRouter 기반 LLM 호출 — 의도 분류 보조 + 종합판단 해석(Reflexion).

CLI/FastAPI 어디서든 그대로 재사용할 수 있도록 input()/print() 등
CLI 전용 코드는 포함하지 않는다.
"""
import json
import os
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

INTENTS = ["종합판단", "체크리스트", "지표조회"]


async def _call_openrouter(messages: list, temperature: float = 0.3) -> Optional[str]:
    """OpenRouter Chat Completions API를 호출해 응답 텍스트만 반환한다(What).
    LLM은 이 프로젝트에서 해석/분류를 "보조"하는 역할이지 핵심 판정 로직이 아니므로,
    호출이 실패해도 예외를 위로 던지지 않고 None을 반환해 호출부가
    "LLM 없이 계속 진행"을 스스로 판단하게 한다(Constraints).
    """
    if not OPENROUTER_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": messages,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


async def llm_classify_intent(question: str) -> Optional[str]:
    """키워드 매칭이 애매할 때(classify_intent_by_keyword가 None을 반환했을 때)만 호출된다(What).
    Args: question — 사용자의 원본 질문 문장(How).
    Returns: "종합판단"/"체크리스트"/"지표조회" 중 하나, 판단 불가하거나 호출 실패 시 None(Output).
    """
    prompt = (
        "다음 질문이 아래 세 가지 의도 중 어디에 해당하는지 정확히 하나만, "
        "다른 말 없이 단어 그대로만 출력해.\n"
        "- 지표조회: 특정 지표 값(EPS, 매출, 이동평균 등)을 묻는 질문\n"
        "- 체크리스트: SEPA 조건을 다 충족하는지 여부를 묻는 질문\n"
        "- 종합판단: 이 종목이 성장주로서 괜찮은지 종합적으로 묻는 질문\n\n"
        f"질문: {question}\n"
        "답변(위 세 단어 중 하나만):"
    )
    result = await _call_openrouter([{"role": "user", "content": prompt}], temperature=0)
    if result is None:
        return None
    for intent in INTENTS:
        if intent in result:
            return intent
    return None


async def llm_interpret_result(ticker: str, growth: dict, trend: dict, sepa: dict) -> dict:
    """SEPA 판정 결과를 사람이 읽을 설명으로 해석한다 — Reflexion(1회 생성 + 1회 자기검증)(What).
    "종합판단" 의도일 때만 파이프라인 마지막 단계에서 호출된다(When).

    Args:
        ticker, growth(calc_growth_metrics 결과), trend(calc_trend_metrics 결과),
        sepa(check_sepa_conditions 결과)(How).

    Returns:
        {"status": "ok", "draft": ..., "final": ...} 또는
        {"status": "llm_unavailable", "message": ...}(Output).

    Note:
        Self-Consistency(같은 질문을 여러 번 생성해 다수결)가 아니라 Reflexion을 쓰는 이유:
        이 해석은 정답이 하나로 정해진 계산이 아니라 개방형 설명이라 다수결이 맞지 않는다
        (설계도 v7 7번 항목). 대신 1차 생성 → "판정 결과와 모순되지 않는지 / 데이터에 없는
        말을 지어내지 않았는지"를 LLM 스스로 점검하는 자기검증 1회로 대체한다.
        LLM 호출이 실패해도 이미 계산된 sepa 판정 자체(코드 계산 결과)는 이 함수와 무관하게
        신뢰할 수 있다 — LLM은 "설명"만 담당하고 "판정"은 코드가 이미 끝낸 상태이기 때문(Constraints).
    """
    facts = {
        "ticker": ticker,
        "sepa_status": sepa.get("status"),
        "unmet_conditions_kr": sepa.get("unmet_conditions_kr", []),
        "eps_growth_q1": growth.get("eps_growth_q1"),
        "eps_growth_q2": growth.get("eps_growth_q2"),
        "revenue_growth_q1": growth.get("revenue_growth_q1"),
        "margin_latest": growth.get("margin_latest"),
        "current_price": trend.get("current_price"),
        "ma50": trend.get("ma50"),
        "ma150": trend.get("ma150"),
        "ma200": trend.get("ma200"),
        "week52_high": trend.get("week52_high"),
        "week52_low": trend.get("week52_low"),
    }
    facts_json = json.dumps(facts, ensure_ascii=False, default=str)

    draft_prompt = (
        "아래는 한 종목의 SEPA(펀더멘털+트렌드템플릿) 판정 결과다. "
        "이 데이터에 있는 숫자와 판정 결과만 근거로 삼아서, 왜 이 판정이 나왔는지 "
        "투자 초보자도 이해할 수 있게 3~5문장으로 한국어로 설명해. "
        "데이터에 없는 정보(경쟁사 비교, 미래 전망 등)는 절대 지어내지 마.\n\n"
        f"데이터: {facts_json}"
    )
    draft = await _call_openrouter([{"role": "user", "content": draft_prompt}], temperature=0.3)
    if draft is None:
        return {
            "status": "llm_unavailable",
            "message": (
                "LLM 호출에 실패해 해석을 생성하지 못했습니다. (.env의 OPENROUTER_API_KEY 확인 필요) "
                "판정 결과 자체는 LLM 없이 코드로 계산된 값이라 그대로 신뢰할 수 있습니다."
            ),
        }

    critique_prompt = (
        "아래는 SEPA 판정 데이터와, 그것을 보고 작성한 설명 초안이다. "
        "이 초안이 (1) 데이터에 없는 사실을 지어내지 않았는지, "
        "(2) 판정 결과(pass/fail)와 모순되는 말을 하지 않았는지 점검하고, "
        "문제가 있으면 고친 최종 버전을, 문제가 없으면 초안을 그대로 최종 버전으로 출력해. "
        "출력은 최종 버전 설명문만, 다른 설명 없이.\n\n"
        f"데이터: {facts_json}\n\n초안: {draft}"
    )
    final = await _call_openrouter([{"role": "user", "content": critique_prompt}], temperature=0)
    if final is None:
        final = draft  # 자기검증 단계만 실패하면 1차 생성 결과라도 보여준다

    return {"status": "ok", "draft": draft, "final": final}
