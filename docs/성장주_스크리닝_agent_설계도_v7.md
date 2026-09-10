# 성장주 스크리닝 AI Agent — 설계도 v7

---

## 1. 무엇을 만드는가

종목명이나 티커를 주면, SEPA 방식(펀더멘털 + 트렌드템플릿)으로 성장주 조건 충족 여부를 판단해주는 Agent. 미국 상장 주식 위주. 종목 하나씩 대화하듯 질의한다.

| 질문 유형 | 예시 | 실행 단계 |
|---|---|---|
| 지표 조회 | "EPS 성장률 알려줘" | 조회 → 계산 |
| 체크리스트 확인 | "조건 다 충족해?" | 조회 → 계산 → 판정 |
| 종합 판단 | "성장주야?" | 조회 → 계산 → 판정 → LLM 해석 |

**Agent인 부분**: 마지막 "종합 판단"(LLM이 판정 결과를 보고 이유를 설명)뿐. 나머지(티커 변환·조회·계산·판정)는 고정 순서로 도는 Workflow — 정답이 정해진 계산은 코드가 하고, 해석이 필요한 부분만 LLM이 맡는다는 원칙.

---

## 2. 전체 구조

```
질문(자연어)
   → [의도 분류] 키워드 규칙 먼저, 애매하면 LLM
   → [조회] 재무데이터·가격데이터 동시 조회 (서로 독립이라 병렬)
   → [계산] 펀더멘털 지표 + 트렌드 지표
   → [판정] 체크리스트 (데이터 부족하면 판정 보류)
   → [Agent] 의도에 맞게 답변 구성 (종합판단이면 LLM 해석 + 자기검증)
```

---

## 3. 판정 기준

**펀더멘털** — EPS 성장률 20%↑(2분기 연속, 직전분기 대비) · 매출 성장률 0%↑(직전분기 대비) · 순이익률 0%↑

**트렌드템플릿** (원문 8개 중 7개, RS지수는 데이터 소스 한계로 1차 제외) — 이동평균선(50/150/200일) 배열, 52주 저가 대비 30%↑, 52주 신고가 대비 25% 이내

> 원문 기준은 성장률을 전년 동기 대비(YoY)로 보는 것이 정통이나, 이번 설계는 의도적으로 직전 분기 대비(QoQ)를 택함 — [발표 전 본인 이유 채워넣기]

---

## 4. 모델 / 데이터 / 도구

- **모델**: OpenRouter 무료 모델 (rate limit 있음 → 재시도 정책으로 보완, 상황 보고 유료 전환 검토)
- **데이터**: yfinance (무료, 미국 주식 대상)
- **도구 6개** (`tools.py`로 구현 완료, 전부 실제 실행 검증함): `get_ticker`(종목명→티커) · `get_quarterly_financials`(재무데이터) · `get_price_history`(가격 히스토리) · `calc_growth_metrics`(펀더멘털 계산) · `calc_trend_metrics`(트렌드 계산) · `check_sepa_conditions`(판정)
- 의도 분류·LLM 종합판단은 도구가 아님 — 전자는 라우팅 로직, 후자는 도구 결과를 받는 쪽이라 "AI가 부르는 함수"가 아님

> ⚠️ **아직 검증 못 한 것**: `calc_growth_metrics`가 참조하는 실제 인덱스명(`"Basic EPS"`, `"Total Revenue"`, `"Net Income"`)은 이 환경 네트워크 제약으로 실 데이터 대조를 못 했음. 노트북에서 `print(financials_data.index)`로 확인 필요.

---

## 5. v6 → v7 수정 사항 9가지

| # | 문제 | 판단 근거 | 결론 |
|---|---|---|---|
| 1 | 키워드 복수 매칭 시 우선순위 없음 | 세 의도는 포함관계(종합판단⊃체크리스트⊃지표조회) — 정보 더 많은 쪽 우선하면 놓치는 게 적음 | 우선순위 리스트 적용 + 대소문자 정규화 (코드 참고) |
| 2 | 의도별 실행 경로 분기 없음 | 지표조회만 원할 때도 전체 계산하면 API 낭비 | 의도별 조기 종료 + 독립적인 두 조회(재무/가격)는 병렬 처리 |
| 3 | API 재시도 정책 미정 | 즉시 재시도는 API를 더 압박함, 무한 재시도는 응답 지연 | 지수 백오프 3회(1→2→4초), 결정론적 실패엔 재시도 안 함 |
| 4 | 도구가 설계 원칙(단일책임·명시적 인자·구조화 반환·docstring)으로 채점 안 됨 | 초반 도구(티커변환 등)를 간단하다고 대충 설계함 | 6개 전부 채점 및 구현 완료 |
| 5 | 데이터 부족 시 판정 방식 미정 | 조건들이 한 세트 필터라 일부 빼고 판정하면 거짓 확신 위험 | 펀더멘털·트렌드 중 하나라도 계산 실패 시 전체 판정 보류 (코드 참고) |
| 6 | 실행 기록(trace) 설계 없음 | 기록 없이는 최종 답이 이상해도 어느 단계 문제인지 못 찾음 | 단계별 기록 스키마만 설계 (실제 저장은 다음 범위) |
| 7 | Self-Consistency와 Reflexion 개념 혼용 | 종합판단은 정답이 하나가 아닌 개방형 설명이라 다수결(Self-Consistency)이 안 맞음 | Reflexion(1회 생성 + 1회 자기검증)으로 확정 |
| 8 | Self-Consistency 반복 횟수 미정 | 7번에서 아예 안 쓰기로 함 | 해당 없음으로 종결 |
| 9 | trace 필드가 기존 로그 형식과 맞는지 미검토 | 필드명을 맞추면 기존 분석 도구를 재사용 가능 | `{question, calls:[{tool, args}], answer}` 형식으로 통일 |
| 10 | (신규) `get_ticker`가 회사명·티커를 잘못 구분함 | 실제 실행해보니 한글은 `.upper()`가 무의미하고, 짧은 영문 회사명이 티커로 오인식됨 | 매핑 테이블 조회를 형태 추측보다 먼저 하도록 순서 변경 |
| 11 | (신규) yfinance가 네트워크 오류를 조용히 삼킴 | `quarterly_financials`는 오류 시에도 예외 없이 빈 값만 반환 — "데이터 없음"과 "네트워크 문제"가 똑같이 보임 | 본 조회 전 가벼운 `history()` 호출로 네트워크 상태 먼저 확인 |
| 12 | (신규) 재무 데이터 인덱스명 미검증 | 이 환경은 네트워크 차단으로 실 데이터 대조 불가 | 코드에 가정으로 명시, 노트북 검증 항목으로 별도 표시 |

**핵심 코드 2개만:**

```python
# (1) 우선순위 + 대소문자 정규화
INTENT_PRIORITY = ["종합판단", "체크리스트", "지표조회"]

def classify_intent(question: str) -> str:
    q = question.lower()
    matched = []
    if any(kw in q for kw in ["괜찮아", "성장주야", "어때"]):
        matched.append("종합판단")
    if any(kw in q for kw in ["체크리스트", "조건 충족"]):
        matched.append("체크리스트")
    if any(kw in q for kw in ["eps", "매출", "성장률", "이동평균", "52주"]):
        matched.append("지표조회")
    if not matched:
        return "LLM_CLASSIFY"
    for intent in INTENT_PRIORITY:
        if intent in matched:
            return intent
```

```python
# (10) get_ticker — 매핑 조회를 형태 추측보다 먼저 (실행 중 발견한 순서 문제 반영)
def get_ticker(query: str) -> dict:
    q = query.strip()
    ticker = name_to_ticker.get(q.lower())          # 1순위: 매핑 테이블 조회
    if ticker:
        return {"status": "found", "ticker": ticker}
    if q.isascii() and q.isalpha() and 1 <= len(q) <= 5:   # 2순위: 매핑에 없을 때만 티커 형식 인정
        return {"status": "found", "ticker": q.upper()}
    return {"status": "not_found", "ticker": None}
```

```python
# (5) 데이터 부족 시 판정 거부 — 실제 구현은 펀더멘털·트렌드 두 결과를 함께 받는 구조
def check_sepa_conditions(fundamental: dict, trend: dict) -> dict:
    if fundamental.get("status") != "ok" or trend.get("status") != "ok":
        return {"status": "insufficient_data",
                "message": "펀더멘털 또는 트렌드 지표 계산에 실패해 판정을 보류합니다."}
    checks = {
        "eps_growth_2q_20pct": fundamental["eps_growth_q1"] >= 0.20 and fundamental["eps_growth_q2"] >= 0.20,
        "revenue_growth_positive": fundamental["revenue_growth_q1"] >= 0,
        # ... 나머지 조건도 같은 패턴
    }
    unmet = [k for k, passed in checks.items() if not passed]
    return {"status": "pass" if not unmet else "fail", "unmet_conditions": unmet}
```

> 전체 도구 구현은 `tools.py` 참고. 재무데이터·가격데이터 동시 조회(asyncio), 재시도 로직은 필요시 별도로 펼쳐서 설명.

---

## 6. 검토했지만 적용 안 한 것 — MCP

11~17강에서 배운 것 중 Parallel(독립 작업 동시 실행)은 위 (2)번에 반영했지만, MCP(도구를 독립 서버로 분리하는 표준)는 적용하지 않음. 판단 기준: 지금 도구는 이 프로젝트 안에서만 쓰이고, 다른 프로젝트·사람과 공유할 계획이 없음 — 이 조건에서는 서버 관리 비용만 늘고 얻는 이득이 없음. 도구 공유 필요가 생기면 재검토.

---

## 7. 알려진 한계 / 다음 단계

- 신규 상장주(1년 미만)는 200일선 관련 조건을 계산할 수 없어 판정 보류됨 — 방법론 자체의 특성
- RS지수(트렌드템플릿 8번)는 유료 데이터라 1차 제외, 근사치 계산 방법 조사 필요
- trace 실제 저장(SQLite)은 다음 범위
- **6개 도구는 코드로 구현하고 가짜(mock) 데이터로 계산 로직만 검증함** — 이 환경이 Yahoo 서버 접속 자체가 막혀있어 실제 데이터로는 못 돌려봄. 노트북에서 실제 데이터로 재검증 필요 (특히 `calc_growth_metrics`의 인덱스명)
- 분기 데이터가 실제로 연속인지(날짜 간격) 검증하는 로직은 아직 미반영 — 다른 프로젝트에서 이미 겪은 문제라 다음 보완 항목
- **발표 전 채울 것**: 섹션 3의 QoQ 선택 이유
