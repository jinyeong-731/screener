# 성장주 스크리닝 CLI (SEPA 방식)

종목명이나 티커를 입력하면 SEPA 방식(펀더멘털 + 트렌드템플릿)으로 성장주 조건 충족 여부를 판단해주는 CLI 프로그램입니다. 미국 상장 주식 위주로, 종목 하나씩 대화하듯 질의합니다.

> 설계 배경과 의사결정 근거는 `성장주_스크리닝_agent_설계도_v7.md` 참고.

## 무엇을 판단하는가

| 질문 유형 | 예시 | 실행 단계 |
|---|---|---|
| 지표 조회 | "애플 EPS 성장률 알려줘" | 티커변환 → 조회 → 계산 |
| 체크리스트 확인 | "테슬라 체크리스트 충족해?" | + 판정 |
| 종합 판단 | "엔비디아 성장주야?" | + LLM 해석 |

**판정 기준**
- 펀더멘털: EPS 성장률 20%↑(2분기 연속, 직전분기 대비 QoQ) · 매출 성장률 0%↑(QoQ) · 순이익률 0%↑
- 트렌드템플릿(7개, RS지수는 유료 데이터라 1차 제외): 이동평균선(50/150/200일) 배열 · 52주 저가 대비 30%↑ · 52주 신고가 대비 25% 이내

데이터가 하나라도 부족하면(재무데이터 계산 실패, 200거래일 미만 등) **판정을 보류**합니다. 일부 조건만으로 "괜찮은 종목"처럼 보이는 답을 주지 않습니다.

## 설치

```bash
cd screener
pip install -r requirements.txt
copy .env.example .env
```

`.env`를 열어 [openrouter.ai/keys](https://openrouter.ai/keys)에서 발급받은 API 키를 넣습니다. **지표조회/체크리스트는 API 키 없이도 동작**하고, 종합판단(LLM 해석)에만 필요합니다.

```
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=meta-llama/llama-3.1-8b-instruct:free
```

## 실행

```bash
python main.py
```

```
질문> 애플 EPS 성장률 알려줘
질문> 테슬라 체크리스트 충족해?
질문> 엔비디아 성장주야?
질문> exit
```

의도가 애매한 질문(키워드 매칭도, LLM 분류도 실패한 경우)은 셋 중 어떤 걸 원하는지 번호로 되묻습니다.

## 프로젝트 구조

```
screener/
├── tools.py       # 도구 6개 (get_ticker, get_quarterly_financials, get_price_history,
│                  #            calc_growth_metrics, calc_trend_metrics, check_sepa_conditions)
├── pipeline.py    # 핵심 로직 — 의도분류·티커추출·조회·계산·판정을 잇는 파이프라인
│                  #   (input()/print() 없음 — 나중에 FastAPI가 run_pipeline()을 그대로 가져다 씀)
├── llm.py         # OpenRouter 호출 — LLM 의도분류 보조 + Reflexion 방식 종합판단 해석
├── main.py        # CLI (input()/print() 전담)
├── .env.example
├── .gitignore
└── requirements.txt
```

`pipeline.py`와 `main.py`를 분리한 이유: 다음 단계에서 이 파이프라인을 FastAPI 서버로 감싸 n8n과 연결할 계획이라, 핵심 로직이 CLI 전용 코드와 섞이지 않아야 그대로 재사용할 수 있습니다.

## 설계 포인트

- **의도별 조기 종료**: 지표조회는 계산까지만, 체크리스트는 판정까지만, 종합판단만 LLM 해석까지 진행합니다. 불필요한 API 호출을 줄이기 위함입니다.
- **재무데이터·가격데이터 병렬 조회**: 서로 독립된 조회라 `asyncio.gather`로 동시에 실행합니다(`tools.py`의 동기 함수는 `asyncio.to_thread`로 감쌈).
- **재시도 정책**: yfinance 호출은 지수 백오프 3회(1→2→4초)로 재시도하며, 네트워크 오류와 "이 종목은 원래 데이터가 없음"을 구분합니다(`tools.py`에 구현).
- **판정 보류 우선**: 펀더멘털·트렌드 중 하나라도 계산에 실패하면 체크리스트/종합판단은 무조건 판정을 보류합니다.
- **Reflexion (Self-Consistency 아님)**: 종합판단의 LLM 해석은 정답이 하나로 정해진 계산이 아니라 개방형 설명이라, 여러 번 생성해 다수결을 내는 Self-Consistency 대신 1회 생성 → 1회 자기검증(판정 결과와 모순되지 않는지, 근거 없는 말을 지어내지 않았는지) 방식을 씁니다.

## 알려진 한계

- 신규 상장주(1년 미만)는 200일선 관련 조건을 계산할 수 없어 판정이 보류됩니다.
- RS지수(트렌드템플릿 8번째 조건)는 유료 데이터라 1차 제외했습니다.
- 종목명 인식은 `tools.py`의 매핑 테이블(애플/테슬라/엔비디아/마이크로소프트 + 영문 회사명 + 영문 티커)에만 의존합니다. 목록에 없는 회사명은 정확한 티커로 질문해야 합니다.
- 분기 데이터가 실제로 연속인지(날짜 간격) 검증하는 로직은 아직 없습니다.
- 실행 기록(trace)은 파이프라인 결과 dict에 담기지만, 별도 DB(SQLite 등)에 저장하는 건 다음 범위입니다.

## 다음 단계 (계획)

이 파이프라인을 FastAPI 서버로 감싸고 n8n과 연결할 예정입니다. `pipeline.run_pipeline(question, intent_override=None)`이 FastAPI 라우터가 그대로 호출할 진입점입니다.
