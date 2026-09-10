"""성장주 스크리닝 CLI — 사용자와 직접 상호작용하는 부분(input/print)만 여기 둔다.
핵심 로직(의도분류~계산~판정~LLM해석)은 전부 pipeline.py에 있고, main.py는
그 결과를 받아 터미널에 보여주는 역할만 한다 (나중에 FastAPI로 감쌀 때
pipeline.py는 그대로, main.py만 라우터로 교체하면 되도록).
"""
import asyncio

from pipeline import run_pipeline


def fmt_pct(value) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:+.1f}%"


def fmt_price(value) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f}"


def print_growth(growth: dict) -> None:
    print("\n[펀더멘털 지표]")
    if growth.get("status") != "ok":
        print(f"  계산 실패: {growth.get('detail')}")
        return
    print(f"  EPS 성장률(직전분기 대비, QoQ): {fmt_pct(growth['eps_growth_q1'])} (그 전 분기: {fmt_pct(growth['eps_growth_q2'])})")
    print(f"  매출 성장률(QoQ): {fmt_pct(growth['revenue_growth_q1'])}")
    print(f"  순이익률: {fmt_pct(growth['margin_latest'])}")


def print_trend(trend: dict) -> None:
    print("\n[트렌드 지표]")
    if trend.get("status") != "ok":
        if trend.get("status") == "insufficient_history":
            print(f"  계산 불가: 거래일 데이터 {trend.get('available_days')}일 (최소 {trend.get('required_days')}일 필요 — 상장 1년 미만 종목일 수 있음)")
        else:
            print(f"  계산 실패: {trend.get('detail')}")
        return
    print(f"  현재가: {fmt_price(trend['current_price'])}")
    print(f"  50일선: {fmt_price(trend['ma50'])} / 150일선: {fmt_price(trend['ma150'])} / 200일선: {fmt_price(trend['ma200'])}")
    print(f"  52주 최고: {fmt_price(trend['week52_high'])} / 52주 최저: {fmt_price(trend['week52_low'])}")


def print_sepa(sepa: dict) -> None:
    print("\n[SEPA 체크리스트 판정]")
    if sepa.get("status") == "insufficient_data":
        print(f"  판정 보류: {sepa.get('message')}")
        return
    status = "충족 (PASS)" if sepa["status"] == "pass" else "미충족 (FAIL)"
    print(f"  결과: {status}")
    if sepa.get("unmet_conditions_kr"):
        print("  미충족 조건:")
        for cond in sepa["unmet_conditions_kr"]:
            print(f"    - {cond}")


def print_interpretation(interpretation: dict) -> None:
    print("\n[종합 판단 해석]")
    if interpretation.get("status") != "ok":
        print(f"  {interpretation.get('message')}")
        return
    print(f"  {interpretation['final']}")


async def handle_question(question: str, intent_override=None) -> None:
    result = await run_pipeline(question, intent_override=intent_override)
    status = result["status"]

    if status == "needs_clarification":
        print("\n질문 의도가 애매합니다. 아래 중 하나를 선택해주세요:")
        for i, candidate in enumerate(result["candidates"], start=1):
            print(f"  {i}. {candidate}")
        choice = input("번호 입력 (그 외 입력 시 취소): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(result["candidates"]):
            chosen_intent = result["candidates"][int(choice) - 1]
            await handle_question(question, intent_override=chosen_intent)
        else:
            print("취소되었습니다.")
        return

    if status == "ticker_not_found":
        print(f"\n{result['message']}")
        return

    if status == "data_unavailable":
        print(f"\n'{result['ticker']}' 데이터를 조회할 수 없습니다.")
        print(f"  재무데이터: {result['financials_error']}")
        print(f"  가격데이터: {result['price_error']}")
        return

    # status == "ok"
    print(f"\n===== {result['ticker']} — {result['intent']} =====")
    if "growth" in result:
        print_growth(result["growth"])
    if "trend" in result:
        print_trend(result["trend"])
    if "sepa" in result:
        print_sepa(result["sepa"])
    if "interpretation" in result:
        print_interpretation(result["interpretation"])
    print()


def main() -> None:
    print("성장주 스크리닝 CLI (SEPA 방식)")
    print("예시: '애플 EPS 성장률 알려줘' / '테슬라 체크리스트 충족해?' / '엔비디아 성장주야?'")
    print("종료하려면 exit 또는 quit 입력\n")

    while True:
        try:
            question = input("질문> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료합니다.")
            break
        if not question:
            continue
        if question.lower() in ("exit", "quit"):
            print("종료합니다.")
            break
        try:
            asyncio.run(handle_question(question))
        except Exception as e:
            print(f"\n예상치 못한 오류가 발생했습니다: {e}")


if __name__ == "__main__":
    main()
