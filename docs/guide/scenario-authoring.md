# Guide: Scenario Authoring

대상: 시나리오 빌더 UI 를 사용하는 테넌트 운영자 / 비즈니스 분석가.

## 시나리오란 무엇인가

시나리오는 **하나의 통화 흐름을 정의한 그래프** 이다.
노드는 대화의 한 단계(질문·분기·외부 호출)이고, 엣지는 전이 조건이다.

런타임에는 사용자의 음성 입력이 STT 를 거쳐 텍스트로 변환되고,
시나리오 그래프의 `entry` 노드부터 시작해 순차적으로 실행된다.

## 시나리오의 생명주기

1. **Draft 저장** — 빌더 UI 에서 `Save`. 서버가 `version` 을 채번.
2. **검증** — Draft 는 ScenarioSchema + `validateGraph` 통과한 상태여야 저장 가능.
3. **Publish** — `Publish` 버튼은 검증 에러가 없을 때만 활성화.
   Publish 된 특정 version 만 런타임에서 로드된다.
4. **Rollback** — 이전에 Publish 된 version 을 다시 Publish 하면 됨.
   Version 번호는 단조 증가 (rollback 후에도 최신 번호가 유지).

## 노드 타입

각 노드는 `id` + `type` + `config` 로 구성된다.
`id` 는 시나리오 내 유일한 식별자. 영문/숫자/언더스코어/하이픈 1~64자.

### `intent`

사용자 입력을 분류해 다음 경로를 결정.
```json
{
  "id": "classify_intent",
  "type": "intent",
  "config": {
    "labels": ["billing", "technical", "default"],
    "model": "groq-llama-3.3-70b",
    "threshold": 0.5
  }
}
```
* `labels` 최소 2개. `"default"` 는 매칭되지 않은 경우의 폴백 라벨.
* `threshold` 는 confidence. 낮을수록 억지로 분류, 높을수록 애매하면 default.

### `llm`

LLM 응답 생성. 통화의 대부분을 차지.
```json
{
  "id": "reply_billing",
  "type": "llm",
  "config": {
    "model": "groq-llama-4-scout",
    "fallback_model": "groq-llama-3.3-70b",
    "system_prompt": "당신은 요금 문의 상담사입니다. ...",
    "temperature": 0.7,
    "max_tokens": 512,
    "streaming": true,
    "enable_filler": true
  }
}
```
* `streaming: true` 권장 — TTS 가 먼저 도착한 토큰부터 합성.
* `enable_filler: true` 면 응답 지연 시 "네, 확인해보겠습니다" 등의 간투어를 자동 재생.
* `system_prompt` 는 한국어 기준 400~800 토큰이 적당. 더 길면 비용·지연 증가.

### `tool`

외부 연동 (계정 조회, 결제, CRM API 등).
```json
{
  "id": "lookup_account",
  "type": "tool",
  "config": {
    "tool_name": "lookup_account",
    "args_template": { "phone": "{{caller_phone}}" },
    "timeout_s": 5.0,
    "retry": 1,
    "on_error": "fallback"
  }
}
```
* `tool_name` 은 `app.connectors.registry` 에 등록된 키. 관리자에게 연동 요청.
* `on_error`:
  * `"fallback"` — 시나리오 `fallback_node` 로 이동 (권장).
  * `"continue"` — 실패를 무시하고 다음 노드. 실패가 비즈니스에 무해할 때만.
  * `"raise"` — 예외 전파 → 통화 종료 가능. 거의 사용하지 말 것.
* `timeout_s` 는 반드시 외부 서비스 SLO 보다 작게 — 5초가 현실적 한계.

### `branch`

조건 분기. 엣지의 `when` 절로 다음 경로를 결정.
```json
{
  "id": "branch_by_intent",
  "type": "branch",
  "config": { "mode": "intent" }
}
```
`mode`:
* `"intent"` — 앞선 `intent` 노드의 결과와 매칭.
* `"slot"` — 특정 슬롯 값으로 매칭 (`slot_key` 지정).
* `"expr"` — 파이썬식 표현 (제한된 허용 함수만).

### `end`

통화 종료. 단일 closing message 를 재생 후 연결 해제.
```json
{ "id": "done", "type": "end", "config": { "closing_message": "감사합니다." } }
```

## 엣지

```json
{ "from": "classify_intent", "to": "reply_billing", "when": "intent == 'billing'" }
```
* `when` 없음 = 무조건 전이.
* `when` 있는 엣지가 먼저, 없는 것이 마지막 fallback 으로 평가.
* `label` 은 빌더 캔버스에서 보이는 문자열 (런타임에 영향 없음).

## 필수 필드

시나리오 단위:

| 필드 | 역할 | 생략 시 |
|---|---|---|
| `scenario_id` | 테넌트 내 유일 식별자 | — 필수 |
| `tenant_id` | 소유 테넌트 | 서버가 JWT claim 으로 강제 overwrite |
| `entry` | 시작 노드 id | — 필수, 그래프에 존재해야 함 |
| `fallback_node` | 예외 시 이동할 노드 id | 없으면 그래프가 터지면 통화 종료 — **강력히 권장** |
| `limits` | max_turns, max_cost 등 | 시스템 기본값 적용 |

## 품질 체크리스트

빌더의 Validation 패널에 다음 체크가 있다:

* `DUPLICATE_NODE_ID` — 같은 id 가 두 번 존재
* `ENTRY_MISSING` — `entry` 로 지정된 id 가 그래프에 없음
* `FALLBACK_MISSING` — `fallback_node` 로 지정된 id 가 그래프에 없음
* `EDGE_FROM_MISSING` / `EDGE_TO_MISSING` — 엣지가 존재하지 않는 노드를 참조
* `UNREACHABLE_NODE` — entry 에서 도달 불가 (warning, fallback 은 예외)

**Publish 전에 모든 error 는 해소되어야 한다** (warning 은 허용).

## 패턴

### 안전한 LLM → Tool → LLM 시퀀스

1. `llm` (사용자 의도 재확인) → 2. `tool` (데이터 조회, `on_error: "fallback"`) → 3. `llm` (조회 결과를 자연어로 전달)

### 확실한 종료 경로

통화가 엉뚱하게 루프에 빠지지 않도록 **모든 경로가 `end` 로 수렴** 하는지 확인.
`max_turns` 를 초과하면 시스템이 강제로 `fallback_node` 또는 `end` 로 이동.

### Multi-lingual

노드 내부 프롬프트/라벨을 언어별 키워드로 분리해 여러 `intent` 노드를 병렬 구성.
이건 LangGraph 의 병렬 분기 기능이 필요 — 현재 DSL 로는 unconditional 두 엣지가 아닌 설계는 어려움. 실험적.

## 버전 관리 권장

* 작은 개선은 기존 `scenario_id` 의 새 version 으로 저장.
* 구조적 변경 (엔드포인트 이동, 컨텍스트 패널 전면 개편) 은 `scenario_id` 를 새로 할당.
  예: `cs_account_v3` → `cs_account_v4`.
* Publish 시 changelog 를 slack #scenario-changes 에 남길 것.

## 관련

* [ADR-001: LangGraph 선택 사유](../adr/ADR-001-langgraph-selection.md)
* [Tenant Onboarding Checklist](./tenant-onboarding.md)
* [LLM Quota 초과 대응 Runbook](../runbook/llm-quota-exceeded.md)
