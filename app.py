"""성장주 스크리닝 웹 UI — Streamlit.

CLI(main.py)와 마찬가지로 핵심 로직은 전혀 없고, pipeline.py의 run_pipeline()을
그대로 가져다 화면에 보여주기만 한다. (pipeline.py를 두 프레젠테이션 레이어가
공유하는 구조 — CLI 쪽과 똑같은 이유로 여기도 판정 로직을 직접 건드리지 않는다)

실행: streamlit run app.py
"""
import asyncio

import streamlit as st

from pipeline import run_pipeline


def fmt_pct(value) -> str:
    return "N/A" if value is None else f"{value * 100:+.1f}%"


def fmt_price(value) -> str:
    return "N/A" if value is None else f"{value:,.2f}"


def render_growth(growth: dict) -> None:
    st.subheader("펀더멘털 지표")
    if growth.get("status") != "ok":
        st.warning(f"계산 실패: {growth.get('detail')}")
        return
    col1, col2, col3 = st.columns(3)
    col1.metric(
        "EPS 성장률 (QoQ)",
        fmt_pct(growth["eps_growth_q1"]),
        f"전분기 {fmt_pct(growth['eps_growth_q2'])}",
        delta_color="off",  # 이 값은 "변화량"이 아니라 참고용 두 번째 수치라, 화살표/색상을 켜두면
                            # 마이너스인데도 초록색 위쪽 화살표가 붙어 오해를 줄 수 있음
    )
    col2.metric("매출 성장률 (QoQ)", fmt_pct(growth["revenue_growth_q1"]))
    col3.metric("순이익률", fmt_pct(growth["margin_latest"]))


def render_trend(trend: dict) -> None:
    st.subheader("트렌드 지표")
    if trend.get("status") == "insufficient_history":
        st.warning(
            f"계산 불가: 거래일 데이터 {trend.get('available_days')}일 "
            f"(최소 {trend.get('required_days')}일 필요 — 상장 1년 미만 종목일 수 있음)"
        )
        return
    if trend.get("status") != "ok":
        st.warning(f"계산 실패: {trend.get('detail')}")
        return
    col1, col2 = st.columns(2)
    col1.metric("현재가", fmt_price(trend["current_price"]))
    col2.metric("52주 최고 / 최저", f"{fmt_price(trend['week52_high'])} / {fmt_price(trend['week52_low'])}")
    st.write(f"50일선: {fmt_price(trend['ma50'])} · 150일선: {fmt_price(trend['ma150'])} · 200일선: {fmt_price(trend['ma200'])}")


def render_sepa(sepa: dict) -> None:
    st.subheader("SEPA 체크리스트 판정")
    if sepa.get("status") == "insufficient_data":
        st.info(f"판정 보류: {sepa.get('message')}")
        return
    if sepa["status"] == "pass":
        st.success("결과: 충족 (PASS)")
    else:
        st.error("결과: 미충족 (FAIL)")
        for cond in sepa.get("unmet_conditions_kr", []):
            st.write(f"- {cond}")


def render_interpretation(interpretation: dict) -> None:
    st.subheader("종합 판단 해석 (LLM)")
    if interpretation.get("status") != "ok":
        st.info(interpretation.get("message"))
        return
    st.write(interpretation["final"])


def render_result(result: dict) -> None:
    status = result["status"]

    if status == "needs_clarification":
        st.warning("질문 의도가 애매합니다. 아래에서 선택해주세요.")
        choice = st.radio("의도 선택", result["candidates"], key="intent_choice")
        if st.button("선택한 의도로 다시 조회"):
            st.session_state["pending_override"] = choice
            st.rerun()
        return

    if status == "ticker_not_found":
        st.error(result["message"])
        return

    if status == "data_unavailable":
        st.error(f"'{result['ticker']}' 데이터를 조회할 수 없습니다.")
        st.write("재무데이터 오류:", result["financials_error"])
        st.write("가격데이터 오류:", result["price_error"])
        return

    st.markdown(f"### {result['ticker']} — {result['intent']}")
    if "growth" in result:
        render_growth(result["growth"])
    if "trend" in result:
        render_trend(result["trend"])
    if "sepa" in result:
        render_sepa(result["sepa"])
    if "interpretation" in result:
        render_interpretation(result["interpretation"])


st.set_page_config(page_title="성장주 스크리닝 (SEPA)", page_icon="📈")
st.title("📈 성장주 스크리닝 (SEPA 방식)")
st.caption("예시: '애플 EPS 성장률 알려줘' · '테슬라 체크리스트 충족해?' · '엔비디아 성장주야?'")

question = st.text_input("질문을 입력하세요", key="question_input")
submitted = st.button("조회")

if submitted and question.strip():
    with st.spinner("조회 중입니다... (데이터/LLM 호출로 몇 초 걸릴 수 있어요)"):
        result = asyncio.run(run_pipeline(question.strip()))
    st.session_state["last_question"] = question.strip()
    st.session_state["last_result"] = result

override = st.session_state.pop("pending_override", None)
if override and st.session_state.get("last_question"):
    with st.spinner("조회 중입니다..."):
        result = asyncio.run(run_pipeline(st.session_state["last_question"], intent_override=override))
    st.session_state["last_result"] = result

if st.session_state.get("last_result"):
    render_result(st.session_state["last_result"])
