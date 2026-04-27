# ADR-001: LangGraph 를 AI 에이전트 오케스트레이션 엔진으로 채택

| 항목 | 값 |
|---|---|
| 상태 | Accepted |
| 작성일 | 2026-01-12 |
| 최종 점검일 | 2026-04-18 |
| 맥락 | Track 0 스캐폴딩 단계 |
| 작성자 | Platform Eng |

## 맥락 (Context)

AgentOE 는 다중 테넌트 실시간 음성 콜봇 플랫폼으로,
단일 통화 세션 내에서 STT → Intent → LLM → Tool 호출 → TTS 를 왕복하며
시나리오 관리자가 시각적 빌더로 정의한 "시나리오 그래프" 를 따라 흐른다.

시나리오 그래프는 런타임에 다음을 요구한다:

* **상태 머신** — 노드 간 전이 + 현재 상태(대기·분기·호출 중) 추적
* **스트리밍 토큰 처리** — LLM 응답을 토큰 단위로 흘려 TTS 로 조기 합성
* **병렬 분기/합류** — 예: Intent 판정과 Filler 응답을 동시에 시작, 먼저 끝난 쪽을 기준으로 다음 노드 결정
* **부분 실패 대응** — Tool 호출 실패 시 그래프 상의 fallback 노드로 이동 (통화가 끊기지 않아야 함)
* **테스트 용이성** — 특정 노드만 모킹하고 그래프 일부만 재생해보는 유닛 테스트

## 고려한 대안

### 1) LangGraph (채택)

* 장점:
  * `StateGraph` + `node` 라는 개념이 시나리오 DSL 과 거의 1:1 매핑.
  * LangChain 생태계 연동 — 툴/리트리버/메모리 그대로 활용.
  * checkpoint 메커니즘이 있어 통화 재개 / 디버그 재생에 유리.
  * Python 3.12 async 네이티브 지원 (stream, acompile).
  * 컴파일 단계가 명시적 — `scenario_compiler.py` 에서 DSL → StateGraph 변환을 단위 테스트로 고정 가능.
* 단점:
  * Python 전용. Node 런타임 확장이 필요해지면 경계 재설계 필요.
  * 정식 릴리스 초기라 breaking change 리스크 (버전 핀 필수).

### 2) LangChain Agents (Routing Agent, OpenAI Functions Agent 등)

* 장점: 보일러플레이트 최소, 프롬프트만으로 동작.
* 단점:
  * 통화 시나리오처럼 **사용자가 그래프를 직접 정의** 해야 하는 경우 Agent 는 너무 많은 결정권을 LLM 에 위임한다 — 재현성·감사 추적이 약해 규제·품질 요구에 취약.
  * 분기 조건이 프롬프트 안에 묻혀 있어 테스트 어려움.

### 3) 직접 구현 (asyncio + dataclass state machine)

* 장점: 의존성 최소, 완전한 제어권.
* 단점:
  * 재구현 비용이 LangGraph 이 주는 이익(체크포인트·스트림·가시화)을 상회.
  * 사내 시나리오 작성자가 LangChain 에코시스템의 컴포넌트를 재사용할 수 없음.

### 4) Temporal / Apache Airflow 계열

* 장점: 긴 수명 워크플로에 강함.
* 단점: 초저지연(수백 ms) 시나리오에는 오버헤드 과다. 주 타깃은 1~10초 이상의 작업.

## 결정

**LangGraph 를 시나리오 실행 엔진으로 채택한다.**

구체적으로:

* `app/agentic/scenario_dsl.py` — Pydantic DSL 정의 (UI 에서 JSON 으로 송신).
* `app/agentic/scenario_compiler.py` — DSL → `langgraph.graph.StateGraph` 컴파일.
* `app/agentic/callbot_graph.py` — 글로벌 컴파일 결과 캐시 + 런타임 호출.
* `app/agentic/router.py` — 컴파일된 그래프를 세션에 주입 + 이벤트 라우팅.

## 결과 (Consequences)

* **긍정**: Track 0.5 에서 시나리오 그래프 end-to-end 통과까지 약 2 주 단축 (직접 구현 대비 추정).
* **긍정**: 테스트는 그래프 compile 결과의 노드 목록·엣지 조건을 assert 하는 식으로 간결 (`tests/unit/test_scenario_compiler.py`).
* **주의**: LangGraph 버전 고정 — `pyproject.toml` 에서 ^0.2 범위로 핀. 메이저 업그레이드 시 ADR 갱신 필수.
* **주의**: checkpoint 백엔드를 Redis 로 연결한 시점부터 Redis 장애가 "통화 자체 끊김" 으로 번지지 않도록, `scenario_compiler` 에서 `ephemeral checkpointer` 를 기본으로 사용 (Redis 는 옵션). 관련: [Runbook: Redis 장애 대응](../runbook/redis-outage.md).

## 재평가 시점

다음 중 하나가 충족되면 이 결정을 재평가한다:

* LangGraph 0.x 가 1.x 로 올라가며 API 를 크게 재편할 때.
* JVM 또는 Go 기반 런타임 도입 논의가 프로덕트 레벨에서 발생할 때.
* 단일 통화 p99 지연이 3초를 상회하여 프레임워크 오버헤드가 의심될 때.
