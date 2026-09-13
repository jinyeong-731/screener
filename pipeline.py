"""성장주 스크리닝 파이프라인 — 핵심 로직 (설계도 v7 기준)

CLI(main.py)와 이후 FastAPI 서버가 동일하게 가져다 쓸 수 있도록,
이 파일에는 input()/print() 같은 CLI 전용 코드를 절대 넣지 않는다.
모든 함수는 async def이며, tools.py의 동기(blocking) 함수는
asyncio.to_thread로 감싸 별도 스레드에서 실행한다.
"""
import asyncio
import math
from typing import Optional

from tools import (
    get_ticker,
    get_quarterly_financials,
    get_price_history,
    calc_growth_metrics,
    calc_trend_metrics,
    check_sepa_conditions,
)
from llm import llm_classify_intent, llm_interpret_result

# 설계도 5번 항목: 세 의도는 포함관계(종합판단 ⊃ 체크리스트 ⊃ 지표조회) —
# 정보를 더 많이 요구하는 쪽을 우선한다.
INTENT_PRIORITY = ["종합판단", "체크리스트", "지표조회"]


def classify_intent_by_keyword(question: str) -> Optional[str]:
    """키워드 규칙으로 의도를 분류한다(What). 설계도 5번 코드 그대로.
    Args: question — 사용자 질문 원문(How).
    Returns: 세 의도 중 하나, 매칭되는 키워드가 하나도 없으면 None(LLM 분류 필요)(Output).
    """
    q = question.lower()
    matched = []
    if any(kw in q for kw in ["괜찮아", "성장주야", "어때"]):
        matched.append("종합판단")
    if any(kw in q for kw in ["체크리스트", "조건 충족"]):
        matched.append("체크리스트")
    if any(kw in q for kw in ["eps", "매출", "성장률", "이동평균", "52주"]):
        matched.append("지표조회")
    if not matched:
        return None
    for intent in INTENT_PRIORITY:
        if intent in matched:
            return intent
    return None


async def classify_intent(question: str) -> dict:
    """의도를 분류한다: 키워드 규칙 우선, 매칭 안 되면 LLM 분류, 그래도 안 되면 사용자 확인 필요(What).
    Returns:
        {"status": "ok", "intent": ..., "method": "keyword"|"llm"} 또는
        {"status": "needs_clarification", "candidates": [...]}(Output).
    """
    intent = classify_intent_by_keyword(question)
    if intent:
        return {"status": "ok", "intent": intent, "method": "keyword"}

    llm_intent = await llm_classify_intent(question)
    if llm_intent in INTENT_PRIORITY:
        return {"status": "ok", "intent": llm_intent, "method": "llm"}

    return {"status": "needs_clarification", "candidates": INTENT_PRIORITY}


_TOKEN_STRIP_CHARS = " ?!.,~'\"()[]{}:;"

# get_ticker의 2순위 규칙("ASCII 알파벳 1~5자면 티커로 간주")은 종목명이 아니라
# 지표 용어("EPS" 등)에도 그대로 걸린다. tools.py는 수정하지 않기로 했으므로,
# 여기서 흔히 쓰이는 지표/금융 약어를 후보 토큰에서 미리 제외해 오검출을 막는다.
# (실제 테스트 중 "모르는회사 EPS 알려줘"가 "EPS"를 티커로 오인식하는 걸 발견해서 추가함)
TICKER_EXTRACTION_STOPWORDS = {
    "eps", "per", "roe", "roa", "pbr", "psr", "cagr",
    "ma", "rs", "qoq", "yoy", "ath", "atl", "ipo",
}


def extract_ticker(question: str) -> dict:
    """질문 문장을 공백 단위로 쪼개, 각 단어를 get_ticker에 시도해 첫 성공을 종목으로 채택한다(What).
    get_ticker는 "애플" 같은 종목명/티커 단독 문자열만 인식하므로("애플 EPS 알려줘" 전체를
    넣으면 매핑에 없어 무조건 not_found), 문장에서 종목명 토큰을 먼저 분리해야 한다(When).
    Args: question — 사용자 질문 원문(How).
    Returns: get_ticker와 동일한 형식 {"status": "found", "ticker": ...} 또는
             {"status": "not_found", "ticker": None}(Output).
    Note: 현재 매핑 테이블(tools.py)의 회사명이 전부 공백 없는 한 단어라 이 방식으로 충분하다.
          "제이피모건" 처럼 공백 섞인 회사명이 매핑 테이블에 추가되면 이 함수도 같이 손봐야 한다.
          TICKER_EXTRACTION_STOPWORDS에 없는 새로운 지표 약어가 실제로 티커로 오인식되면
          이 목록에 추가해야 한다 — 근본 원인은 get_ticker가 "형식만 티커 같으면" 통과시키는
          느슨한 규칙이라 완벽히 막을 수는 없다(Constraints).
    """
    for token in question.split():
        cleaned = token.strip(_TOKEN_STRIP_CHARS)
        if not cleaned or cleaned.lower() in TICKER_EXTRACTION_STOPWORDS:
            continue
        result = get_ticker(cleaned)
        if result["status"] == "found":
            return result
    return {"status": "not_found", "ticker": None}


async def _fetch_financials(ticker: str) -> dict:
    return await asyncio.to_thread(get_quarterly_financials, ticker)


async def _fetch_prices(ticker: str) -> dict:
    return await asyncio.to_thread(get_price_history, ticker)


def _drop_nan_closes(closes: list) -> list:
    """종가 리스트에서 NaN(결측치)을 제거한다(What).
    calc_trend_metrics를 부르기 전에 항상 거쳐야 한다(When) — 실제 NVDA 데이터로 테스트하다
    발견한 문제: yfinance가 반환하는 1년치 종가 중 단 하나만 NaN이어도, calc_trend_metrics가
    쓰는 파이썬 기본 max()/min()은 리스트에 NaN이 있으면 그 이후 비교가 전부 깨져
    최종 결과가 통째로 nan이 되어버린다(파이썬 max/min의 잘 알려진 함정). 그 nan이
    check_sepa_conditions에서는 "계산 성공(status=ok)"으로 통과해버려서, 원래는
    "판정 보류"돼야 할 상황이 엉뚱하게 FAIL/PASS로 잘못 나온다.
    tools.py는 수정하지 않기로 했으므로, calc_trend_metrics에 넘기기 전
    여기서 미리 걸러낸다(Constraints).
    """
    return [c for c in closes if not math.isnan(c)]


async def run_pipeline(question: str, intent_override: Optional[str] = None) -> dict:
    """전체 파이프라인을 실행한다(What). CLI와 FastAPI가 공통으로 호출하는 진입점(When).

    Args:
        question — 사용자 원문 질문.
        intent_override — 의도 분류가 애매해 사용자에게 되물은 뒤, 사용자가 직접 고른
                           의도를 넘길 때 사용(선택)(How).

    Returns:
        status가 "needs_clarification"/"ticker_not_found"/"data_unavailable"/"ok" 중 하나인
        결과 dict. "ok"일 때 intent에 따라 growth/trend/sepa/interpretation 키가 단계적으로
        추가된다(설계도 2번 항목 "의도별 조기 종료")(Output).

    Note:
        설계도 9번 항목의 trace 스키마({question, calls, answer})를 그대로 따르되,
        실제 저장(SQLite)은 이번 범위 밖이라 결과 dict 안에만 담아 반환한다(Constraints).
    """
    trace = {"question": question, "calls": []}

    # 1) 의도 분류
    if intent_override:
        intent = intent_override
        trace["calls"].append({"tool": "classify_intent", "args": {"override": intent_override}})
    else:
        classify_result = await classify_intent(question)
        trace["calls"].append({"tool": "classify_intent", "args": {"question": question}})
        if classify_result["status"] == "needs_clarification":
            return {
                "status": "needs_clarification",
                "candidates": classify_result["candidates"],
                "trace": trace,
            }
        intent = classify_result["intent"]

    # 2) 질문에서 종목명/티커 추출 + 표준 티커 변환
    ticker_result = extract_ticker(question)
    trace["calls"].append({"tool": "get_ticker", "args": {"question": question}})
    if ticker_result["status"] != "found":
        return {
            "status": "ticker_not_found",
            "intent": intent,
            "message": f"'{question}'에서 종목을 특정할 수 없습니다. 정확한 종목명이나 티커를 알려주세요.",
            "trace": trace,
        }
    ticker = ticker_result["ticker"]

    # 3) 재무데이터·가격데이터는 서로 독립이므로 asyncio.gather로 동시 조회 (설계도 2번 항목)
    financials_result, price_result = await asyncio.gather(
        _fetch_financials(ticker),
        _fetch_prices(ticker),
    )
    trace["calls"].append({"tool": "get_quarterly_financials", "args": {"ticker": ticker}})
    trace["calls"].append({"tool": "get_price_history", "args": {"ticker": ticker}})

    financials_ok = financials_result.get("status") == "ok"
    price_ok = price_result.get("status") == "ok"

    if not financials_ok and not price_ok:
        return {
            "status": "data_unavailable",
            "intent": intent,
            "ticker": ticker,
            "financials_error": financials_result,
            "price_error": price_result,
            "trace": trace,
        }

    # 4) 계산 — 조회가 실패한 쪽은 calc_* 함수를 호출하지 않고 바로 error 상태로 취급
    #    (calc_growth_metrics/calc_trend_metrics는 각각 DataFrame/list를 기대하므로
    #     실패 결과 dict를 그대로 넘기면 안 된다)
    if financials_ok:
        growth = calc_growth_metrics(financials_result["data"])
    else:
        growth = {"status": "error", "detail": financials_result.get("error", "FETCH_FAILED")}
    trace["calls"].append({"tool": "calc_growth_metrics", "args": {}})

    if price_ok:
        trend = calc_trend_metrics(_drop_nan_closes(price_result["closes"]))
    else:
        trend = {"status": "error", "detail": price_result.get("error", "FETCH_FAILED")}
    trace["calls"].append({"tool": "calc_trend_metrics", "args": {}})

    # === 지표조회 의도: 여기서 조기 종료 (설계도 2번 항목) ===
    if intent == "지표조회":
        return {
            "status": "ok",
            "intent": intent,
            "ticker": ticker,
            "growth": growth,
            "trend": trend,
            "trace": trace,
        }

    # 5) 체크리스트 판정 — 설계도 5번 항목: 데이터 하나라도 부족하면 판정 보류
    sepa_result = check_sepa_conditions(growth, trend)
    trace["calls"].append({"tool": "check_sepa_conditions", "args": {}})

    # === 체크리스트 의도: 여기서 조기 종료 ===
    if intent == "체크리스트":
        return {
            "status": "ok",
            "intent": intent,
            "ticker": ticker,
            "growth": growth,
            "trend": trend,
            "sepa": sepa_result,
            "trace": trace,
        }

    # === 종합판단 의도: LLM 해석까지 진행 (Reflexion) ===
    interpretation = await llm_interpret_result(ticker, growth, trend, sepa_result)
    trace["calls"].append({"tool": "llm_interpret_result", "args": {"method": "reflexion"}})

    return {
        "status": "ok",
        "intent": intent,
        "ticker": ticker,
        "growth": growth,
        "trend": trend,
        "sepa": sepa_result,
        "interpretation": interpretation,
        "trace": trace,
    }
