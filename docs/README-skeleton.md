# AgentOE Documentation

운영·온보딩·아키텍처 의사결정을 추적하는 저장소입니다.
코드에서 파생 가능한 정보(심볼 참조, 호출 그래프 등)는 최대한 배제하고,
**"코드만 읽어서는 모르는 맥락"** 만 기록합니다.

## 구조

| 폴더 | 종류 | 설명 |
|---|---|---|
| `adr/` | Architecture Decision Records | 왜 이 기술/패턴을 선택했는지 (불변·타임스탬프 고정) |
| `runbook/` | Operations Runbooks | 장애 탐지부터 복구까지 단계별 대응 절차 |
| `guide/` | How-to Guides | 개발자/테넌트 관리자가 일상적으로 참고하는 작업 가이드 |
| `reference/` | Reference Catalogs | 메트릭/API/설정 목록 — 타 문서에서 링크 대상 |

## 쓸 때 규칙

* **언어**: 한국어 본문 + 영문 고유 명사/심볼 그대로.
* **타임스탬프**: 문서 상단에 "작성일" + "최종 점검일" 을 Kubernetes Node 교체 주기마다 업데이트.
* **코드 링크**: 절대 경로 (`backend/app/core/redis_client.py`) 로 표기.
  파일 이동 시 `rg "redis_client\.py"` 한 번으로 모두 찾을 수 있도록.
* **ADR 은 수정하지 말 것**: 새 상황이 생기면 새 ADR 을 만들어 이전 번호를 `Superseded by: ADR-###` 로 연결.

## 인덱스

### ADR

* [ADR-001: LangGraph 를 AI 에이전트 오케스트레이션 엔진으로 채택](./adr/ADR-001-langgraph-selection.md)
* [ADR-002: 멀티테넌트 키 네임스페이스 설계](./adr/ADR-002-tenant-key-namespace.md)

### Runbook

* [Redis 장애 대응](./runbook/redis-outage.md)
* [DLQ 처리 절차](./runbook/dlq-processing.md)
* [JWKS kid 회전](./runbook/jwks-kid-rotation.md)
* [Kill-switch 운영](./runbook/kill-switch-ops.md)
* [LLM Quota 초과 대응](./runbook/llm-quota-exceeded.md)

### Guide

* [Scenario Authoring](./guide/scenario-authoring.md)
* [Tenant Onboarding Checklist](./guide/tenant-onboarding.md)

### Reference

* [Prometheus 메트릭 카탈로그](./reference/prometheus-metrics.md)
