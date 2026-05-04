# Agentic AI Module (LangGraph PoC)

AgentOE 의 AI 파이프라인을 **LangGraph 기반 Agentic AI** 로 전환하기 위한 PoC 스켈레톤.
기존 `app/domain/ai_pipeline.py` 는 그대로 유지하고, 본 모듈은 **Strangler Fig** 패턴으로
테넌트/시나리오 단위 opt-in 을 통해 점진적으로 전환한다.

## 구조

```
app/agentic/
├── state.py                 # CallbotState (TypedDict, 그래프 전역 상태)
├── scenario_dsl.py          # Pydantic DSL (JSON/YAML 시나리오 스키마)
├── scenario_compiler.py     # DSL → LangGraph StateGraph 컴파일
├── callbot_graph.py         # 런타임 (compile 캐시, stream_turn, resume)
├── router.py                # Strangler Fig 라우팅 (legacy vs agentic)
├── nodes/
│   ├── intent_node.py       # 인텐트 분류 (Groq Llama 3.3)
│   ├── llm_node.py          # 응답 생성 (스트리밍 + Filler)
│   ├── tool_node.py         # 외부 도구 호출 (registry 기반)
│   ├── branch_node.py       # 조건 분기 (intent/slot/expr)
│   ├── transfer_node.py     # 상담원 전환
│   ├── wait_node.py         # 사용자 입력 대기 (interrupt)
│   ├── context_node.py      # 슬롯 업데이트
│   └── end_node.py          # 정상 종료
└── scenarios/
    └── example_customer_service.json  # 샘플 시나리오
```

## 실행 흐름

```
WebSocket 턴 수신
   │
   ▼
AgenticRouter.decide(tenant_id, scenario_id, session_id)
   │
   ├─ use_agentic=False ──► 기존 AIPipeline.process()
   │
   └─ use_agentic=True  ──► CallbotGraphRuntime.stream_turn()
                              │
                              ├─ _get_compiled(): scenario DSL 로드 → LangGraph 컴파일 (LRU 캐시)
                              │
                              └─ graph.astream(input, config):
                                  각 노드의 state update event 를 WS 로 푸시
                                  Wait 노드 도달 시 interrupt (Redis Checkpointer 저장)
```

## 시나리오 DSL 간단 예

```json
{
  "scenario_id": "cs_account_v3",
  "tenant_id": "t_acme",
  "version": 3,
  "entry": "greeting",
  "fallback_node": "fallback_speak",
  "nodes": [
    {"id": "greeting", "type": "llm", "config": {...}},
    {"id": "classify", "type": "intent", "config": {"labels": [...]}},
    {"id": "router",   "type": "branch", "config": {"mode": "intent"}}
  ],
  "edges": [
    {"from": "greeting", "to": "classify"},
    {"from": "classify", "to": "router"},
    {"from": "router", "when": "billing", "to": "respond_billing"},
    {"from": "router", "when": "default", "to": "fallback_speak"}
  ]
}
```

## 의존성

```
pip install langgraph>=0.2 langgraph-checkpoint-redis pydantic>=2.5
```

**주의**: langgraph 가 설치되지 않은 환경에서는 `compile_scenario()` 가 dry-run
(구조 검증만) 모드로 동작합니다. 프로덕션 배포 전 반드시 위 패키지 설치 필요.

## 안전 가드 (CLAUDE.md 준수)

- 모든 노드는 **async** (비동기 I/O)
- Tool 실패 시 `on_error: fallback` → `fallback_node` 로 우아하게 분기
- LLM CircuitBreaker OPEN → 정중한 대기 메시지 + 상담원 전환 제안
- `AgenticRouter.decide()` 가 예외 발생 시 즉시 legacy 로 폴백

## 향후 작업 (Sprint 로드맵 참조)

- [ ] Redis Checkpointer 통합 (`langgraph-checkpoint-redis`)
- [ ] Scenario Registry Repository (MongoDB CRUD + 버전 관리)
- [ ] REST/GraphQL API (`/api/v1/scenarios`) — 시나리오 빌더 UI 연동
- [ ] 노드 단위 관측 (OpenTelemetry span per node)
- [ ] 테넌트 비용 쿼터 적용 (limits.max_cost_cents_per_session)
