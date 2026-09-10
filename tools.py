"""성장주 스크리닝 agent — 도구 모음 (설계도 v7 기준)"""


def get_ticker(query: str) -> dict:
    """종목명(한글/영문) 또는 이미 정확한 티커를 미국 증시 티커로 변환한다(What).
    사용자가 "애플", "Apple", "AAPL" 등 어떤 형태로 질문해도
    이후 단계(데이터 조회)에 넘길 표준 티커가 필요할 때 사용한다(When).

    Args:
        query: 한글 회사명, 영문 회사명, 또는 티커 (예: "애플", "Apple", "AAPL")(How).

    Returns:
        {"status": "found", "ticker": "AAPL"} 또는
        {"status": "not_found", "ticker": None}(Output).

    Note:
        매핑 테이블에 없는 이름은 추측으로 비슷한 티커를 반환하지 않는다.
        이미 대문자 3~5자 형태(티커 형식)면 매핑 없이 그대로 통과시킨다(Constraints).
    """
    name_to_ticker = {
        "애플": "AAPL", "apple": "AAPL",
        "테슬라": "TSLA", "tesla": "TSLA",
        "엔비디아": "NVDA", "nvidia": "NVDA",
        "마이크로소프트": "MSFT", "microsoft": "MSFT",
    }
    q = query.strip()

    # 1순위: 매핑 테이블에 회사명으로 등록돼 있는지 먼저 확인 (한글/영문 회사명 모두 여기서 걸림)
    ticker = name_to_ticker.get(q.lower())
    if ticker:
        return {"status": "found", "ticker": ticker}

    # 2순위: 매핑에 없고, ASCII 알파벳 1~5자면 이미 티커를 직접 입력한 것으로 간주
    if q.isascii() and q.isalpha() and 1 <= len(q) <= 5:
        return {"status": "found", "ticker": q.upper()}

    return {"status": "not_found", "ticker": None}


def get_quarterly_financials(ticker: str, max_retries: int = 3) -> dict:
    """분기별 재무 데이터(EPS·매출·순이익)를 조회한다(What).
    Args: ticker — get_ticker가 반환한 표준 티커(How).
    Returns: {"status": "ok", "data": ...} 또는
             {"error": "GROUNDING"/"TOOL_RELIABILITY", "detail": ...}(Output).
    Note: quarterly_financials 프로퍼티는 네트워크 오류 시에도 예외 없이 빈 DataFrame을
          반환하는 라이브러리 특성이 있어, 먼저 가벼운 history() 호출로 네트워크 상태를
          확인한 뒤 본 데이터를 조회한다 — 이렇게 안 하면 "네트워크 오류"와
          "이 종목은 원래 데이터가 없음"이 똑같이 빈 값으로 보여 구분이 안 된다(Constraints).
    """
    import time
    import yfinance as yf

    wait_seconds = 1
    for attempt in range(max_retries):
        try:
            t = yf.Ticker(ticker)
            t.history(period="5d")  # 네트워크 상태 확인용 — 실패하면 여기서 예외가 던져짐

            data = t.quarterly_financials
            if data is None or data.empty:
                return {"error": "GROUNDING", "detail": "NO_DATA_FOR_TICKER"}  # 네트워크는 정상, 데이터만 없음
            return {"status": "ok", "data": data}
        except Exception as e:
            if attempt == max_retries - 1:
                return {"error": "TOOL_RELIABILITY", "detail": str(e), "retries_exhausted": True}
            print(f"  [재시도 {attempt + 1}/{max_retries}] {wait_seconds}초 대기 후 재시도... ({type(e).__name__})")
            time.sleep(wait_seconds)
            wait_seconds *= 2


def get_price_history(ticker: str, max_retries: int = 3) -> dict:
    """최소 1년치 일별 종가 히스토리를 조회한다(What).
    트렌드템플릿(이동평균선, 52주 고저가) 계산 전, 원재료 가격 데이터가 필요할 때 사용(When).
    Args: ticker — get_ticker가 반환한 표준 티커(How).
    Returns: {"status": "ok", "closes": [...]} 또는
             {"error": "GROUNDING"/"TOOL_RELIABILITY", "detail": ...}(Output).
    Note: get_quarterly_financials와 동일하게 네트워크 오류와 데이터 없음을 구분한다.
          period="1y"만 요청하면 실제 거래일 기준 약 252일치가 온다 — 200일선 계산에 필요한
          최소치(200일)보다 여유 있게 확보하기 위함(Constraints).
    """
    import time
    import yfinance as yf

    wait_seconds = 1
    for attempt in range(max_retries):
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="1y")
            if hist is None or hist.empty:
                return {"error": "GROUNDING", "detail": "NO_PRICE_DATA_FOR_TICKER"}
            return {"status": "ok", "closes": hist["Close"].tolist()}
        except Exception as e:
            if attempt == max_retries - 1:
                return {"error": "TOOL_RELIABILITY", "detail": str(e), "retries_exhausted": True}
            print(f"  [재시도 {attempt + 1}/{max_retries}] {wait_seconds}초 대기 후 재시도... ({type(e).__name__})")
            time.sleep(wait_seconds)
            wait_seconds *= 2


def calc_growth_metrics(financials_data, required_quarters: int = 3) -> dict:
    """최근 분기들의 QoQ(직전분기 대비) EPS·매출 성장률과 순이익률을 계산한다(What).
    체크리스트 판정 전, "2분기 연속 20%" 조건에 필요한 실제 성장률 값이 필요할 때 사용(When).
    Args: financials_data — get_quarterly_financials가 반환한 data
          (pandas DataFrame, 컬럼=분기, 인덱스=계정과목명)(How).
    Returns: {"status": "ok", "eps_growth_q1": ..., "eps_growth_q2": ...,
              "revenue_growth_q1": ..., "margin_latest": ...} 또는
             {"status": "error", "detail": ...}(Output).
    Note: yfinance의 정확한 인덱스명("Basic EPS", "Total Revenue", "Net Income" 등)은
          버전이나 종목에 따라 다를 수 있다. 이 환경은 네트워크가 막혀 실제 데이터로
          검증하지 못했으므로, 노트북에서 print(financials_data.index)로 실제
          인덱스명을 먼저 확인하고 아래 INDEX_NAMES가 맞는지 대조할 것(Constraints).
    """
    INDEX_NAMES = {
        "eps": "Basic EPS",
        "revenue": "Total Revenue",
        "net_income": "Net Income",
    }

    try:
        cols = financials_data.columns  # yfinance 기본 정렬: 최근 분기가 0번, 과거로 갈수록 인덱스 증가
        if len(cols) < required_quarters:
            return {"status": "error", "detail": "NOT_ENOUGH_QUARTERS"}

        eps = financials_data.loc[INDEX_NAMES["eps"]]
        revenue = financials_data.loc[INDEX_NAMES["revenue"]]
        net_income = financials_data.loc[INDEX_NAMES["net_income"]]

        # 스크리너 프로젝트에서 이미 겪은 함정: 분기가 실제로 연속인지 확인 필요
        # (여기서는 인덱스 위치만 사용 — 날짜 간격 검증은 다음 보완 항목으로 명시)
        eps_growth_q1 = (eps.iloc[0] - eps.iloc[1]) / abs(eps.iloc[1])
        eps_growth_q2 = (eps.iloc[1] - eps.iloc[2]) / abs(eps.iloc[2])
        revenue_growth_q1 = (revenue.iloc[0] - revenue.iloc[1]) / abs(revenue.iloc[1])
        margin_latest = net_income.iloc[0] / revenue.iloc[0]

        return {
            "status": "ok",
            "eps_growth_q1": eps_growth_q1,
            "eps_growth_q2": eps_growth_q2,
            "revenue_growth_q1": revenue_growth_q1,
            "margin_latest": margin_latest,
        }
    except (KeyError, IndexError, ZeroDivisionError) as e:
        return {"status": "error", "detail": str(e)}


def calc_trend_metrics(price_history_closes: list) -> dict:
    """일별 종가 리스트로 이동평균선(50/150/200일)과 52주 고저가를 계산한다(What).
    체크리스트의 트렌드템플릿 조건 판정 전, 원재료 지표가 필요할 때 사용(When).
    Args: price_history_closes — get_price_history가 반환한 종가 리스트,
          과거→현재 순으로 정렬되어 있다고 가정(How).
    Returns: {"status": "ok", "current_price": ..., "ma50": ..., "ma150": ..., "ma200": ...,
              "week52_high": ..., "week52_low": ...} 또는
             {"status": "insufficient_history", "available_days": ..., "required_days": 200}(Output).
    Note: 200일선 계산에는 최소 200거래일이 필요하다. 부족하면 계산하지 않고 명시적으로
          거부한다 — 상장 1년 미만 종목은 이 조건들을 원천적으로 계산할 수 없음(Constraints).
    """
    closes = price_history_closes
    if len(closes) < 200:
        return {
            "status": "insufficient_history",
            "available_days": len(closes),
            "required_days": 200,
        }

    current_price = closes[-1]
    ma50 = sum(closes[-50:]) / 50
    ma150 = sum(closes[-150:]) / 150
    ma200 = sum(closes[-200:]) / 200

    # 52주(약 252거래일) 데이터가 다 있으면 그만큼, 없으면 있는 만큼만 사용
    week52_window = closes[-252:] if len(closes) >= 252 else closes
    week52_high = max(week52_window)
    week52_low = min(week52_window)

    return {
        "status": "ok",
        "current_price": current_price,
        "ma50": ma50,
        "ma150": ma150,
        "ma200": ma200,
        "week52_high": week52_high,
        "week52_low": week52_low,
    }


CONDITION_NAMES = {
    "eps_growth_2q_20pct": "EPS 성장률 2분기 연속 20% 이상(QoQ)",
    "revenue_growth_positive": "매출 성장률 0% 이상(QoQ)",
    "margin_positive": "순이익률 0% 이상",
    "price_above_ma150_200": "현재가 150일·200일선 위",
    "ma150_above_ma200": "150일선이 200일선 위",
    "price_above_ma50": "현재가 50일선 위",
    "price_30pct_above_52w_low": "52주 저가 대비 30% 이상",
    "price_within_25pct_of_52w_high": "52주 신고가 대비 25% 이내",
}


def check_sepa_conditions(fundamental: dict, trend: dict) -> dict:
    """펀더멘털·트렌드 지표로 8개(RS지수 제외 7개) 조건을 판정한다(What).
    지표 계산이 끝난 뒤 최종 pass/fail 판정이 필요할 때 사용(When).
    Args: fundamental — calc_growth_metrics 결과, trend — calc_trend_metrics 결과(How).
    Returns: {"status": "pass"/"fail", "unmet_conditions_kr": [...]} 또는
             {"status": "insufficient_data", "message": ...}(Output).
    Note: 둘 중 하나라도 계산 자체가 실패했으면(status != "ok") 조건 검사 없이 바로
          판정 보류 — 일부 조건만으로 판정하면 근거가 불충분한 채 답을 주는 셈이 된다(Constraints).
    """
    if fundamental.get("status") != "ok" or trend.get("status") != "ok":
        return {
            "status": "insufficient_data",
            "message": "펀더멘털 또는 트렌드 지표 계산에 실패해 판정을 보류합니다.",
        }

    checks = {
        "eps_growth_2q_20pct": fundamental["eps_growth_q1"] >= 0.20 and fundamental["eps_growth_q2"] >= 0.20,
        "revenue_growth_positive": fundamental["revenue_growth_q1"] >= 0,
        "margin_positive": fundamental["margin_latest"] >= 0,
        "price_above_ma150_200": trend["current_price"] > trend["ma150"] and trend["current_price"] > trend["ma200"],
        "ma150_above_ma200": trend["ma150"] > trend["ma200"],
        "price_above_ma50": trend["current_price"] > trend["ma50"],
        "price_30pct_above_52w_low": trend["current_price"] >= trend["week52_low"] * 1.30,
        "price_within_25pct_of_52w_high": trend["current_price"] >= trend["week52_high"] * 0.75,
    }

    unmet = [key for key, passed in checks.items() if not passed]
    return {
        "status": "pass" if not unmet else "fail",
        "unmet_conditions": unmet,
        "unmet_conditions_kr": [CONDITION_NAMES[k] for k in unmet],
    }
