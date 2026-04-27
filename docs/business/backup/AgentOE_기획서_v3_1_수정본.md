# AgentOE 기획서 v3.1 수정본

## 문서 목적

본 문서는 [AgentOE_기획서_개선판_v2.docx](/Users/kchul199/Desktop/project/antigravity_project/AgentOE/docs/AgentOE_기획서_개선판_v2.docx) 및 [AgentOE_기획서_v3_수정안.md](/Users/kchul199/Desktop/project/antigravity_project/AgentOE/docs/AgentOE_기획서_v3_수정안.md)을 기반으로, 실제 콜봇 플랫폼 구축 시 필요한 **기술적 완성도, 구조적 유연성, 운영 안정성**을 강화한 v3.1 개정 초안이다.

이번 v3.1은 다음 원칙을 추가로 반영한다.

1. 실시간 음성 제어와 AI 추론 경로를 분리하여 저지연성을 보호한다.
2. 세션 일관성, 장애 복구, 중복 처리 방지 등 운영 중 핵심 실패 지점을 구조적으로 통제한다.
3. STT/TTS/LLM/API/MCP 커넥터를 벤더 중심이 아니라 계약 중심으로 설계한다.
4. 플랫폼의 확장성과 멀티테넌트 운영성을 tenant 수준이 아니라 실제 운영 격리 수준으로 강화한다.
5. 장애 발생 시 원상 복구보다 먼저 고객 피해를 줄일 수 있도록 degraded mode와 kill switch를 설계에 포함한다.

---

## 1. 우선 반영 권고사항

### 최우선 반영

1. `업무행위 위험등급 + 정책 게이트` 섹션 신설
2. `MCP 거버넌스` 섹션 신설
3. KPI/완료 기준 단일화
4. 개인정보보호 및 외부 벤더 처리 문구를 법무 검토 전제 표현으로 수정
5. 멀티테넌트 전략을 `tenant_id` 수준에서 `운영 격리 수준`으로 확장

### 기술적 완성도 강화를 위한 추가 반영

1. 실시간 `Voice Control Path`와 `Inference Path` 분리
2. 세션 소유권, lease, replay를 포함한 세션 일관성 모델 정의
3. VBGW-AgentOE 프로토콜과 Tool schema의 버전 호환 전략 추가
4. 의존성별 degraded mode matrix 추가
5. 용량 계획, admission control, RTO/RPO, DR 시나리오 추가

### 운영 강화를 위한 추가 반영

1. 상담사 협업 시 CTI/큐/전환 실패 처리 상세화
2. 운영 Kill Switch 및 tenant별 강제 폴백 정책 추가
3. 품질 회귀 테스트 및 Go-Live 게이트 추가
4. 비용 모델 및 용량 계획 부록 추가
5. synthetic canary call, chaos drill, nightly replay test 운영 체계 추가

---

## 2. 교체 또는 신설 권장 문안

## 2.1 9.4 멀티테넌트 격리 전략 개정안

기존 `tenant_id 기반 격리` 문장을 아래와 같이 교체하는 것을 권장한다.

### 9.4 멀티테넌트 격리 전략

AgentOE는 초기 설계 단계부터 멀티테넌트 운영을 전제로 하며, 단순한 `tenant_id` 컬럼 분리 수준이 아니라 다음 5개 계층에서 격리를 구현한다.

1. **데이터 격리**
   - 세션 로그, 프롬프트, 커넥터 설정, 감사 로그, 통화 녹취 메타데이터는 tenant 단위로 논리 분리한다.
   - 필요 시 대형 고객사는 전용 DB schema 또는 전용 저장소 버킷으로 분리할 수 있도록 설계한다.

2. **보안 격리**
   - tenant별 API Key, OAuth Secret, MCP credential, 암호화 키를 별도 저장하고, 중앙 KMS 또는 Secret Manager를 통해 관리한다.
   - 운영자는 자신의 권한 범위 내 tenant 정보만 조회할 수 있도록 RBAC와 ABAC를 함께 적용한다.

3. **운영 격리**
   - tenant별 rate limit, 동시 세션 수, 벤더 quota, 프롬프트 버전, 배포 ring을 독립적으로 운영한다.
   - 특정 tenant의 장애나 과부하가 다른 tenant에 영향을 주지 않도록 worker pool, queue, 캐시 리소스를 분리 또는 제한한다.

4. **품질 격리**
   - tenant별 시나리오, TTS voice, STT 사전, 업무 정책, 상담사 이관 규칙을 개별 관리한다.
   - KPI는 tenant별 대시보드로 제공하여 고객사 단위 SLA 관리가 가능하도록 한다.

5. **정책 격리**
   - 개인정보 보관 기간, 마스킹 수준, 외부 벤더 사용 허용 범위, MCP 사용 범위는 tenant별 정책으로 관리한다.

이 구조를 통해 AgentOE는 단일 플랫폼으로 운영되더라도 고객사별 보안, 운영, 품질 요구사항을 충족할 수 있다.

---

## 2.2 11.2 하위에 `업무행위 위험등급 및 정책 게이트` 섹션 신설안

### 11.2.1 업무행위 위험등급 및 정책 게이트

AgentOE는 LLM의 자율 판단만으로 업무를 수행하지 않으며, 모든 Tool 호출은 정책 게이트를 통과해야 한다. 정책 게이트는 다음 4개 기준을 검증한다.

1. 고객 인증 수준
2. 업무 행위 위험등급
3. 허용 Tool 및 허용 파라미터
4. 상담사 승인 필요 여부

업무행위는 아래와 같이 분류한다.

| 등급 | 예시 | AI 단독 처리 | 추가 조건 |
|---|---|---|---|
| G1 조회형 | 배송조회, 영업시간, 단순 FAQ | 가능 | 인증 불필요 또는 약한 인증 |
| G2 조건부 조회형 | 계약내역, 청구금액, 예약상태 | 조건부 가능 | 고객 식별 및 최소 인증 필요 |
| G3 변경형 | 주소 변경, 배송지 수정, 예약 변경 | 제한적 가능 | 강한 인증 + 파라미터 검증 |
| G4 금전/민감형 | 환불, 결제, 개인신용, 분쟁 | 원칙적 불가 | 상담사 승인 또는 즉시 이관 |
| G5 법무/사고형 | 민원, 분쟁, 보안 사고, 유출 문의 | 불가 | 상담사 또는 전담 조직 이관 |

정책 게이트는 다음 순서로 동작한다.

1. 현재 세션의 인증 상태 확인
2. 의도에 매핑된 업무행위 등급 확인
3. 허용 Tool 목록 및 허용 파라미터 스키마 검증
4. 개인정보 전달 범위 및 마스킹 정책 적용
5. 승인 필요 여부 판단
6. 통과 시 Tool 호출, 미통과 시 안내 후 상담사 이관 또는 안전한 대체 응답 수행

이 구조를 통해 AgentOE는 Agentic AI의 유연성을 유지하면서도 금전, 민감정보, 법무 리스크가 큰 업무를 통제한다.

---

## 2.3 `MCP 거버넌스` 섹션 신설안

### 11.3.1 MCP 거버넌스 및 Tool 통제

외부 MCP Server는 AgentOE의 Tool Layer 하위에서 표준화된 도구 공급원으로 수용하되, 무제한 연결을 허용하지 않는다. AgentOE는 MCP 사용에 대해 다음 정책을 적용한다.

1. **서버 등록 승인제**
   - 등록된 MCP Server만 사용 가능
   - 서버별 소유 부서, 운영 주체, 인증 방식, 제공 Tool 목록을 메타데이터로 관리

2. **허용 Tool 화이트리스트**
   - tenant별, 시나리오별, 업무행위 등급별 허용 Tool을 명시
   - 탐색 가능한 Tool과 실제 호출 가능한 Tool을 분리 관리

3. **세션 범위 자격 증명**
   - 장기 자격 증명을 LLM에 직접 노출하지 않음
   - 세션 단위 임시 토큰 또는 프록시 자격 증명 사용

4. **입출력 검증**
   - Tool 입력값은 JSON Schema 또는 내부 DTO로 검증
   - Tool 결과는 민감정보 마스킹 후 Agent Runtime에 전달

5. **감사 추적**
   - 어떤 세션에서 어떤 MCP Server의 어떤 Tool을 어떤 파라미터로 호출했는지 감사 로그에 기록
   - 실패, timeout, 차단된 호출도 함께 기록

6. **운영 제어**
   - MCP Server 장애 시 자동 차단 및 fallback 적용
   - tenant 또는 서비스 단위로 특정 MCP Tool을 비활성화할 수 있는 Kill Switch 제공

이 정책을 통해 AgentOE는 MCP 생태계를 활용하면서도 무분별한 도구 사용과 데이터 유출 위험을 낮춘다.

---

## 2.4 11.1 `세션 오케스트레이션` 보강 문안

### 11.1.2 운영 관점의 추가 세션 이벤트

실제 전화 운영 환경에서는 음성 턴 전환 외에도 다음 이벤트를 세션 엔진에서 표준적으로 처리해야 한다.

- `TRANSFER_REQUESTED`
- `TRANSFER_ACCEPTED`
- `TRANSFER_FAILED`
- `CALLBACK_SCHEDULED`
- `VENDOR_DEGRADED`
- `TOOL_TIMEOUT`
- `POLICY_BLOCKED`
- `KILL_SWITCH_TRIGGERED`

이 이벤트를 세션 상태 모델에 포함해야 실제 운영 중 장애 분석과 자동 복구가 가능하다.

---

## 2.5 12. 비기능 요구사항 및 KPI 정합화 개정안

### 12.1 운영 KPI 단일 기준표

| 항목 | MVP 목표 | 2단계 목표 | 측정 기준 |
|---|---|---|---|
| STT 정확도 | 93% 이상 | 95% 이상 | 주간 샘플 100콜 수동 검증 |
| FAQ형 응답 지연 P50 | 1.0초 이내 | 0.8초 이내 | 발화 종료~AI 발화 시작 |
| FAQ형 응답 지연 P95 | 2.0초 이내 | 1.5초 이내 | 동일 |
| 조회형 응답 지연 P95 | 3.0초 이내 | 2.5초 이내 | CRM/ERP 호출 포함 |
| 상담사 이관률 | 25% 이하 | 10~15% 이하 | 이관 사유 자동 분류 |
| 플랫폼 가용성 | 99.5% 이상 | 99.9% 이상 | tenant별 SLA 기준 |
| CSAT | 3.8 이상 | 4.0 이상 | 일일 설문 |
| 동시 통화 처리 | 100콜 | 200콜 이상 | soak test 기준 |

### 12.2 KPI 운영 원칙

1. 문서 내 모든 완료 기준은 위 KPI 표를 단일 기준으로 참조한다.
2. 마케팅/제안용 수치와 운영/품질 게이트 수치를 분리하지 않는다.
3. KPI는 tenant별, 업무 유형별, 시간대별로 분해 가능해야 한다.

---

## 2.6 15장 하위에 `운영 Kill Switch 및 강제 폴백` 신설안

### 15.5 운영 Kill Switch 및 강제 폴백

콜봇 운영에서는 문제가 발생했을 때 원인을 분석하는 것보다, 먼저 고객 피해를 줄이는 것이 중요하다. AgentOE는 다음 운영 제어 기능을 제공해야 한다.

1. tenant 단위 Kill Switch
2. 기능 단위 Kill Switch
3. 시나리오 단위 Kill Switch
4. 프롬프트/콘텐츠 롤백
5. 강제 폴백 전략

강제 폴백 전략은 아래를 포함한다.

- AI 응답 대신 사전 승인된 고정 멘트로 전환
- 실시간 응답 대신 콜백 예약 흐름으로 전환
- 상담사 큐 과부하 시 예약 접수 또는 업무시간 안내로 전환

이 기능은 운영 콘솔에서 즉시 실행 가능해야 하며, 실행 이력은 모두 감사 로그에 기록한다.

---

## 2.7 상담사 협업 섹션 보강안

### 26.5 CTI/큐 연동 운영 원칙

상담사 이관은 단순 API 호출이 아니라 실제 컨택센터 큐 운영과 연결되어야 한다. AgentOE는 다음 시나리오를 지원해야 한다.

1. **Warm Transfer**
2. **Blind Transfer**
3. **Queue Wait Handling**
4. **Transfer Failure Handling**
5. **Night/Off-hour Handling**

이관 성공률, 이관 대기시간, 이관 후 평균 처리시간도 별도 KPI로 추적한다.

---

## 2.8 27장 개인정보/컴플라이언스 문안 수정안

### 27. 개인정보보호 및 컴플라이언스 정책

AgentOE는 음성, STT 텍스트, 고객 식별정보, 외부 시스템 조회 결과를 처리하므로 개인정보보호법, 업권별 규제, 고객사 내부 보안정책을 함께 준수해야 한다. 본 장의 정책은 **법무 및 개인정보보호 담당 조직의 최종 검토를 전제로 한 운영 초안**이다.

### 27.1 외부 벤더 사용 원칙

- 외부 STT/TTS/LLM 벤더 활용 시, 해당 관계가 `처리위탁`인지 `제3자 제공`인지 계약 구조와 처리 목적에 따라 구분하여 검토한다.
- 개인정보 처리방침, 위수탁 계약, 국외 이전 여부, 안전조치 의무는 고객사 정책 및 법무 검토 결과에 따라 확정한다.
- **실시간 데이터 마스킹 (Lightweight PII Redaction)**: 외부 벤더(특히 해외 클라우드형 LLM/STT) 연동 시 개인식별정보(주민번호, 계좌번호, 전화번호 등)가 원본 상태로 전송/저장되지 않도록, `Speech Orchestrator` 직후 및 LLM 전송 직전에 엣지 기반의 실시간 마스킹(NER 파이프라인 또는 정규식 패턴 치환)을 강제해야 한다.

### 27.2 보관 및 폐기 원칙

- 음성 원본, STT 텍스트, 감사 로그, 인증 정보의 보관 기간은 **관련 법령, 업권 규제, 고객사 계약조건**에 따라 구분 적용한다.
- 문서에 예시 기간을 둘 경우, 반드시 `예시 기준`임을 명시하고 실제 운영 전 법무 확정을 받는다.

### 27.3 사고 대응 원칙

- 개인정보 유출 또는 오처리 사고 발생 시, 현행 법령과 고객사 계약에 따라 통지, 신고, 보존 조치를 수행한다.
- 세부 신고 기한과 보고 체계는 운영 매뉴얼에 별도 정리하고 정기 훈련을 수행한다.

이렇게 기술하면 문서의 방향성은 유지하면서도 법규를 과도하게 단정하는 위험을 줄일 수 있다.

---

## 2.9 `운영 검증 및 Go-Live 게이트` 섹션 신설안

## 29. 운영 검증 및 Go-Live 게이트

AgentOE는 개발 완료만으로 운영 개시하지 않으며, 아래 항목을 모두 통과해야 Go-Live 가능 상태로 판단한다.

### 29.1 기능 검증

1. 대표 시나리오 50~100건에 대한 골든 대화셋 회귀 테스트 통과
2. 상담사 이관, 콜백 예약, 인증 실패, 민감업무 차단 시나리오 검증
3. 콘텐츠 변경 후 회귀 테스트 자동화

### 29.2 음성 품질 검증

1. 배경잡음 환경 테스트
2. 패킷 손실/지터 환경 테스트
3. 중첩 발화 및 barge-in 테스트
4. 장시간 통화 시 메모리 누수 및 지연 증가 여부 확인

### 29.3 장애 대응 검증

1. STT/TTS/LLM 벤더 장애 시 대체 경로 전환 확인
2. MCP Server timeout 및 외부 API 장애 시 fallback 확인
3. Kill Switch 실행 및 롤백 절차 검증

### 29.4 운영 준비도 검증

1. 운영팀, 상담사, 콘텐츠팀 교육 완료
2. 모니터링 대시보드 및 알람 채널 구성 완료
3. 인시던트 대응 연락체계 수립
4. 법무/보안 승인 완료
5. **로컬 모의 테스트 (Mocking Pipeline)**: 실제 연동망(VBGW 및 외부 통신망)이 제한된 오프라인 개발 환경에서도 시나리오 테스트가 가능하도록, Control Plane 내에 Headless VBGW Simulator와 Dummy MCP/Tool Provider를 기본 구성요소로 확보하여 검증한다.

---

## 2.10 실시간 처리 경로 분리 아키텍처 신설안

### 9.5 Real-time Voice Control Path와 Inference Path 분리

AgentOE는 외부 LLM, Tool, MCP Server의 지연이 실시간 통화 제어 품질을 훼손하지 않도록 두 개의 경로를 분리해 설계한다.

| 경로 | 역할 | 주요 특성 |
|---|---|---|
| Voice Control Path | barge-in, playback stop, VAD event, session control | 초저지연, bounded queue, drop-safe |
| Inference Path | STT, LLM, Tool, MCP, TTS generation | 비동기, 재시도 가능, timeout budget 관리 |

설계 원칙은 다음과 같다.

1. `STOP_AUDIO`, `BARGE_IN`, `HANGUP`, `TRANSFER_REQUESTED`와 같은 제어 이벤트는 LLM 또는 Tool 호출과 무관하게 즉시 처리되어야 한다.
2. Inference Path는 Voice Control Path에 직접 종속되지 않으며, 세션 상태를 참조하되 독립적인 worker pool에서 처리한다.
3. 두 경로 사이에는 bounded queue를 두고, queue overflow 시에는 신규 추론 요청을 제한하거나 degraded mode로 전환한다.
4. 각 턴에는 전체 timeout budget을 부여하고, STT, Agent, Tool, TTS 단계별로 세부 예산을 분할한다.
5. **지연 방어 및 Filler Audio 주입 (Speculative Execution)**: 추론 혹은 외부 API(Tool) 호출이 설정된 Budget(예: 1~2초)을 초과할 경우, LLM의 완전한 결과가 나오기 전이라도 "네, 조회 중입니다"와 같은 경과 안내음(Filler Audio)을 먼저 스트리밍하여 사용자 이탈 및 침묵을 방지하는 구조를 포함한다.

이 구조를 통해 AgentOE는 고지연 외부 의존성이 있더라도 통화 제어 품질을 유지할 수 있다.

---

## 2.11 세션 일관성 및 소유권 모델 신설안

### 11.1.3 세션 일관성 및 소유권 모델

운영 중 worker 장애, 네트워크 분리, 중복 이벤트 수신이 발생할 수 있으므로 AgentOE는 세션 일관성 모델을 명시해야 한다.

핵심 원칙은 다음과 같다.

1. **Single Authoritative Owner**
   - 한 세션은 한 시점에 하나의 worker만 authoritative owner가 된다.

2. **Lease 기반 소유권**
   - 세션 owner는 Redis lease를 주기적으로 갱신하며, lease 만료 시 다른 worker가 takeover할 수 있다.

3. **Append-only Event Log 및 비동기 상태 전이**
   - 주요 세션 이벤트는 append-only 방식으로 저장하여 replay와 장애 분석이 가능하도록 한다.
   - 단, 매 턴의 부분 상태 갱신은 Redis/DB 병목을 피하기 위해 Local In-memory(버퍼)에서 우선 관리하고, 주요 시그널(Turn Finalized 등) 발생 시 비동기 일괄 기록(Async Write-behind / Event-sourcing)하는 방식을 원칙으로 한다.

4. **Idempotency**
   - `CALL_STARTED`, `TRANSFER_REQUESTED`, `PLAYBACK_DONE`, `TOOL_RESULT` 등 주요 이벤트는 idempotency key를 가져야 한다.

5. **Duplicate Suppression**
   - VBGW 재전송 또는 외부 시스템 중복 callback에 대비하여 중복 이벤트를 감지하고 한 번만 반영한다.

6. **Takeover Recovery**
   - owner 장애 시 대체 worker는 최근 checkpoint와 event log를 읽어 세션을 복구한다.

이 모델이 없으면 장시간 통화, 상담사 이관, 외부 의존성 지연 상황에서 세션 중복 처리와 상태 불일치가 발생할 수 있다.

---

## 2.12 프로토콜 및 스키마 버전 전략 신설안

### 14.3 인터페이스 및 스키마 버전 관리 전략

AgentOE는 벤더 교체와 단계적 업그레이드를 고려하여 모든 인터페이스를 버전 있는 계약으로 관리한다.

관리 대상은 다음과 같다.

1. VBGW-AgentOE 이벤트 프로토콜 버전
2. Tool input/output schema 버전
3. MCP capability descriptor 버전
4. Prompt template version
5. Connector SDK version

운영 원칙은 다음과 같다.

1. 하위 호환 가능한 필드 추가는 minor version으로 관리한다.
2. 의미 변경이나 필수 필드 변경은 major version으로 분리한다.
3. 특정 tenant만 새로운 protocol 또는 connector를 먼저 적용할 수 있도록 ring deployment를 지원한다.
4. protocol negotiation 또는 compatibility matrix를 문서화하여 VBGW와 AgentOE가 다른 버전으로도 일정 기간 공존할 수 있어야 한다.

이 전략을 통해 특정 고객사만 점진적으로 업그레이드하거나, 일부 connector만 교체하는 유연한 운영이 가능해진다.

---

## 2.13 의존성별 Degraded Mode Matrix 신설안

### 15.6 Dependency Degradation Matrix

AgentOE는 장애 시 단순 재시도보다 의존성별로 다른 degraded mode를 적용해야 한다.

| 의존성 | 장애 징후 | 자동 대응 | 고객 응대 방식 | 운영자 조치 |
|---|---|---|---|---|
| STT | 오류율 급증, partial 미수신 | 대체 STT 벤더 전환 | 재질문 또는 고정 멘트 | 벤더 상태 점검 |
| LLM | 지연 증가, timeout | FAQ 우선 모드 전환 | 간단 응답 또는 상담사 이관 | prompt/token 정책 조정 |
| Tool/API | timeout, 5xx | circuit breaker open | 조회 지연 안내, 콜백 예약 | 업스트림 복구 확인 |
| MCP Server | handshake 실패, 응답 지연 | MCP Tool 차단 | 핵심 업무만 유지 | tool whitelist 재조정 |
| TTS | 생성 지연, 품질 저하 | pre-generated phrase 사용 | 승인된 고정 멘트 재생 | 대체 voice 전환 |
| CTI/상담사 큐 | 큐 포화, transfer fail | human-first 중지 | 콜백 예약 또는 업무시간 안내 | 큐/인력 상태 점검 |
| Redis/State Store | lease 갱신 실패 | 신규 세션 제한 | 진행 중 세션 우선 보호 | failover 전환 |

또한 AgentOE는 운영 모드를 아래와 같이 구분할 수 있어야 한다.

- `Normal Mode`
- `Degraded AI Mode`
- `Human-first Mode`
- `Maintenance Mode`

---

## 2.14 용량 계획 및 Admission Control 신설안

### 12.3 Capacity Planning and Admission Control

플랫폼이 100콜, 200콜, 500콜 이상으로 확장될 때를 대비해 다음 용량 계획 항목을 명시한다.

1. 동시 통화 수(CCU)
2. 초당 세션 시작 수(CPS)
3. 벤더별 요청 한도와 병렬 스트림 한도
4. Redis ops/sec, queue depth, DB write throughput
5. 녹취 저장소 사용량 증가율

Admission control 정책은 다음과 같다.

1. queue depth, vendor timeout, worker CPU, memory가 임계치를 넘으면 신규 고난도 세션을 제한한다.
2. 민감 업무나 Tool-heavy 업무는 우선 차단하고 조회형 FAQ 우선 모드로 전환한다.
3. tenant별 동시 세션 quota를 적용하여 noisy neighbor를 방지한다.
4. overload 상황에서는 고객에게 지연 사실을 안내하고 상담사 이관 또는 콜백 예약으로 우회한다.

이 정책을 통해 AgentOE는 장애 직전까지 버티는 구조가 아니라, 과부하를 제어 가능한 상태로 운영할 수 있다.

---

## 2.15 DR 및 Failover 전략 신설안

### 15.7 Disaster Recovery, RTO, RPO

99.9% 가용성을 목표로 하는 경우, 단순 이중화가 아니라 복구 목표와 장애 전환 방식을 정의해야 한다.

권장 전략은 다음과 같다.

1. AgentOE stateless worker는 multi-AZ 배치
2. Redis는 Sentinel 또는 Cluster 기반 failover 구성
3. PostgreSQL 또는 핵심 저장소는 replica 및 정기 백업 운영
4. 음성 파일 저장소는 다중 AZ 또는 내구성 높은 object storage 사용
5. 관측 시스템 장애 시에도 최소 운영 모드가 유지되도록 핵심 제어와 관측 컴포넌트를 분리

권장 목표는 다음과 같다.

| 항목 | 권장 목표 |
|---|---|
| RTO | 15분 이내 |
| RPO | 5분 이내 |
| 세션 상태 손실 | 진행 중 세션 최소화, 신규 세션 제한 후 복구 |
| 녹취/감사 로그 손실 | 허용하지 않음 또는 법무 기준에 따름 |

이 항목을 문서화하면 가용성 목표가 단순 선언이 아니라 실제 설계 기준으로 기능한다.

---

## 2.16 관측성 및 운영 검증 체계 보강안

### 15.8 SLO 기반 관측성과 운영 드릴

Distributed tracing만으로는 충분하지 않으며, 운영 중 실제 고객 경험을 반영하는 지표를 별도로 관리해야 한다.

추가 권장 지표는 다음과 같다.

1. user-perceived turn latency
2. barge-in stop latency
3. transfer success rate
4. tool failure rate by dependency
5. degraded mode activation count
6. tenant별 error budget 소비율

권장 운영 활동은 다음과 같다.

1. synthetic canary call을 5분 또는 10분 주기로 수행
2. 주간 chaos drill로 STT/LLM/API 장애 전환 테스트 수행
3. nightly replay test로 대표 대화셋 자동 회귀 검증
4. 주요 장애의 postmortem을 표준 템플릿으로 관리

---

## 2.17 데이터 모델 및 리플레이 구조 보강안

### 10.1 핵심 데이터 모델 권장안

운영 추적성, 품질 개선, 장애 재현을 위해 데이터 모델을 세션 중심으로 세분화하는 것이 좋다.

권장 엔터티는 다음과 같다.

| 엔터티 | 설명 |
|---|---|
| session | 통화 단위의 최상위 엔터티 |
| turn | 고객 발화와 시스템 응답의 한 턴 |
| utterance | STT partial/final 결과와 원문 메타데이터 |
| policy_decision | 정책 게이트 판단 결과 |
| tool_call | API/MCP 호출 요청 및 결과 |
| transfer_event | 상담사 이관 관련 이벤트 |
| audit_event | 관리자/시스템 액션 감사 로그 |
| artifact | 녹취, 프롬프트 버전, 리플레이 스냅샷 |

이 구조를 사용하면 세션 리플레이, 실패 패턴 분석, tenant별 품질 개선 활동이 쉬워진다.

---

## 2.18 성능 최적화 전략 보강안

### 15.9 성능 최적화 우선순위

현재 문서의 `STT 캐시 히트율` 중심 접근은 실제 음성 통화 환경에서 효율이 제한적일 수 있다. 대신 아래 우선순위를 권장한다.

1. 고정 멘트 TTS pre-generation
2. FAQ intent/result cache
3. prompt compression 및 요약 메모리
4. Tool 호출 병렬화와 timeout budget 최적화
5. 벤더 스트림 재사용과 커넥션 풀 최적화
6. partial STT 사용 기준 튜닝

이 방향이 통화 품질과 비용 효율 모두에 더 실질적인 효과를 준다.

---

## 2.19 안전한 배포 및 점진적 롤아웃 전략 신설안

### 28.5 Safe Deployment and Progressive Rollout

콘텐츠와 프롬프트만이 아니라 connector, policy, protocol 변경도 안전하게 배포할 수 있어야 한다.

권장 전략은 다음과 같다.

1. tenant별 canary rollout
2. 기능 플래그 기반 활성화
3. blue-green 또는 ring deployment
4. 자동 rollback trigger
5. protocol compatibility check

자동 rollback은 다음 조건을 만족할 때 발동할 수 있다.

- 이관률 급증
- 응답 지연 P95 급등
- 특정 Tool 오류율 급증
- CSAT 급락

---

## 2.20 SaaS 운영 핵심 항목 상세 반영안

SaaS 구독형 운영에서 장애/보안/확장 리스크를 줄이기 위해 아래 6개 항목을 기획 기준으로 고정한다.

### 2.20.1 멀티테넌시 모델 설계 상세

#### 격리 계층 기준

| 계층 | 필수 기준 |
|---|---|
| 요청 컨텍스트 | `token.tenant_id == header.tenant_id == resource.tenant_id` 강제 |
| 데이터 | row-level 기본 + 대형 고객은 schema/bucket 분리 옵션 |
| 실행 리소스 | tenant별 queue, rate limit, 동시 세션 quota, worker pool 제한 |
| 네트워크/연동 | tenant별 connector allowlist, egress 정책, MCP visibility 분리 |
| 배포/릴리즈 | tenant별 ring, feature flag, canary 독립 운영 |

#### 격리 티어

| 티어 | 대상 | 특징 |
|---|---|---|
| Shared | SMB/기본형 | 논리 격리 + quota |
| Dedicated-Logical | 중대형 | 전용 schema/queue/cache namespace |
| Dedicated-Physical | 규제/대기업 | 전용 저장소/컴퓨트/네트워크 경계 |

### 2.20.2 암호화 정책 상세

| 구간 | 기준 |
|---|---|
| In-Transit | 외부 TLS 1.2+, 내부 서비스 mTLS 권장 |
| At-Rest | DB/Object/Queue 저장 시 AES-256 기반 암호화 |
| Field-Level | 주민번호/계좌/토큰 등 고위험 필드는 별도 컬럼 암호화 |
| Key Lifecycle | 키 버전관리, 주기적 rotation, 즉시 revoke/rollback 절차 |

추가 원칙:

1. 기본은 플랫폼 KMS를 사용하되, 엔터프라이즈는 BYOK 옵션을 제공한다.
2. 세션 처리 컴포넌트는 평문 민감정보를 로그에 남기지 않는다.
3. 암호화 실패 시 fail-open 금지, 요청 차단 또는 안전 fallback 적용.

### 2.20.3 감사로그 상세

#### 감사 이벤트 분류

- 인증/권한 변경
- 정책/프롬프트/커넥터 변경
- 고위험 Tool 호출 및 차단
- 운영자 강제 조치(kill switch, rollback)
- 데이터 접근/내보내기/삭제

#### 필수 필드

`audit_id`, `tenant_id`, `actor`, `action`, `target`, `trace_id`, `result`, `reason`, `timestamp`

#### 무결성 기준

1. append-only 저장
2. 해시 체인 또는 서명 기반 위변조 탐지
3. 삭제 금지 보존 영역(WORM 또는 동등한 통제)

### 2.20.4 데이터 보관/삭제 상세

#### 보관 클래스 예시

| 클래스 | 대상 | 기본 보관 |
|---|---|---|
| R1 | 운영 로그/메트릭 | 30~90일 |
| R2 | 세션 transcript/요약 | 6~12개월 |
| R3 | 녹취/감사 증적 | 계약/규제 기준(예: 1~5년) |

#### 삭제 절차

1. 삭제 요청 접수(tenant 관리자 또는 정책 기반 만료)
2. 영향 범위 검증(법무 hold, 분쟁 hold 확인)
3. 비동기 삭제 실행(원본/파생/캐시 동시 정리)
4. 재검증 및 삭제 완료 증적 발급

### 2.20.5 운영 관측성 상세

#### 필수 SLI

| 카테고리 | SLI |
|---|---|
| 성능 | turn latency P50/P95, barge-in stop latency |
| 품질 | intent accuracy, transfer success rate |
| 안정성 | dependency error rate, degraded mode 횟수 |
| 운영 | tenant error budget, approval timeout rate |

#### 운영 원칙

1. 지표는 tenant/시나리오/시간대 기준으로 분해 조회 가능해야 한다.
2. SLO burn-rate 알람을 사용해 임계치 초과 전 조기 대응한다.
3. synthetic canary + nightly replay + chaos drill을 정기 운영한다.

### 2.20.6 확장 가능한 아키텍처 상세

| 항목 | 설계 기준 |
|---|---|
| Stateless Scale-Out | Session Edge/Runtime/Control Plane 수평 확장 |
| Partitioning | session/tenant 키 기반 shard 및 queue 분산 |
| Backpressure | queue depth, timeout, CPU 기준 admission control |
| Async 분리 | 비실시간 작업(callback/summary/export) 워커 분리 |
| Storage 확장 | hot/warm/cold 계층화 + 수명주기 정책 |

용량 게이트 권장:

1. 100/200/500 CCU 단계별 부하 테스트를 배포 게이트로 고정
2. 장애 전환(RTO/RPO) 리허설을 릴리즈 조건으로 포함
3. 특정 tenant 과부하가 전체 플랫폼으로 전파되지 않도록 격리 한도를 강제

---

## 3. 별도 부록으로 추가 권장하는 항목

### A. 비용 모델 부록

- 동시 100콜, 200콜, 500콜 기준 STT/TTS/LLM 비용 추정
- 벤더별 과금 방식 비교
- 상담사 절감 인건비 추정
- 야간 운영 대체 효과

### B. 용량 계획 부록

- 예상 CPS/CCU
- STT/TTS 벤더 API 한도
- Redis/DB/Queue 예상 부하
- 녹취 저장소 사용량 추정

### C. 운영 KPI 대시보드 예시

- 실시간: 응답 지연, 이관률, 벤더 에러율, 동시호
- 일일: CSAT, 실패 패턴 Top 10, Tool timeout Top 10
- 주간: 시나리오 개선 우선순위, 상담사 이관 사유 분석

### D. DR 및 장애 훈련 부록

- 월간 failover drill
- 분기별 법무/보안 사고 훈련
- 반기별 대규모 벤더 장애 전환 훈련

---

## 4. 문서 편집 순서 제안

실제 v2 문서를 수정할 때는 아래 순서로 편집하는 것이 가장 효율적이다.

1. KPI 및 완료 기준 정합화
2. 정책 게이트와 업무행위 위험등급 추가
3. MCP 거버넌스 추가
4. 멀티테넌트 전략 보강
5. 실시간 처리 경로 분리와 세션 일관성 모델 추가
6. 프로토콜/스키마 버전 관리 전략 추가
7. degraded mode, admission control, DR 전략 추가
8. 개인정보/컴플라이언스 문구 수정
9. 상담사 협업 및 CTI 연동 상세화
10. 운영 검증 및 Go-Live 게이트 추가
11. 비용 모델과 용량 계획 부록 추가

---

## 5. 최종 코멘트

v3.1의 핵심은 AgentOE를 단순한 AI 연동 미들웨어가 아니라, **실시간 음성 제어와 복합 업무 오케스트레이션을 안정적으로 수행하는 상용 콜봇 플랫폼**으로 보이게 만드는 데 있다.

특히 다음 4가지가 문서의 설득력을 크게 높인다.

1. `Voice Control Path`와 `Inference Path`를 분리하여 실시간성을 보호하는 점
2. 세션 소유권, lease, replay를 포함한 일관성 모델을 정의한 점
3. degraded mode, kill switch, admission control로 장애를 통제 가능한 상태로 운영하는 점
4. protocol/schema versioning, tenant 격리, connector contract로 유연성을 확보한 점

이 수준까지 정리되면 AgentOE 기획서는 기술 제안서, 운영 설계서, 플랫폼 확장 전략 문서의 역할을 동시에 수행할 수 있다.

---

## 6. 벤더 선택 및 구현 착수 조건 확정 (v3.1 추가)

본 섹션은 구현 착수 직전 검토 결과를 반영하여 확정된 기술 스택과 착수 조건을 기록한다.

---

### 6.1 구현 착수 전제 조건 확정

| 항목 | 상태 | 비고 |
|---|---|---|
| 법무/보안 검토 | MVP 단계 적용 보류 | 외부 벤더(Groq, Google, Microsoft) 사용으로 처리위탁 계약(DPA) 검토 필요. 상용화 전 확정. |
| VBGW | 자체 개발 | AgentOE 프로토콜에 맞춰 별도 커스텀 개발 진행 중 |
| STT/TTS/LLM 벤더 | **확정 완료** | 아래 6.2 참조 |
| MVP 범위 | 확정 필요 | 팀 내 범위 문서 서명 후 착수 |

**결론: MVP 범위 확정 즉시 구현 착수 가능.**

> ⚠️ **무료 한도 운영 범위**: 아래 벤더의 무료 한도는 **MVP/PoC 단계(동시 통화 10~20콜 수준) 전용**이다. 동시 통화 100콜 이상의 상용 운영을 위해서는 각 벤더의 유료 요금제 전환이 필수이다. 상용화 일정에 맞춰 비용 모델 부록(부록 A)을 업데이트하고 요금제를 확정해야 한다.

---

### 6.2 AI 벤더 선택 확정

AgentOE는 각 AI 컴포넌트에 대해 **Primary + Fallback** 이중 벤더 구조를 채택한다.
이는 아키텍처 설계서의 `Degraded Mode Matrix` 및 `capability-based routing`과 직결된다.

#### STT (Speech-to-Text)

| 구분 | 벤더 | 서비스 | 무료 한도 | 스트리밍 | 한국어 |
|---|---|---|---|---|---|
| Primary | **Groq** | Whisper Large v3 Turbo | 무제한(속도 제한) | ✅ | ✅ |
| Fallback | **Google** | Cloud Speech-to-Text | 60분/월 | ✅ | ✅ 최고 품질 |

선택 근거:
- Groq Whisper는 응답 속도가 가장 빠르며 콜봇 turn latency budget 준수에 유리하다.
- Google Cloud STT는 한국어 정확도가 가장 높으며, Groq 장애 시 자동 전환 대상이다.

#### LLM

| 구분 | 벤더 | 모델 | 무료 한도 | 스트리밍 | 한국어 |
|---|---|---|---|---|---|
| Primary | **Groq** | Llama 4 Scout / Llama 3.3 70B | 30 RPM (1,800 req/시) / 14,400 req/일 (모델별 상이) | ✅ | ✅ |
| Fallback | **Google** | Gemini 2.0 Flash | 1,500 req/일 (AI Studio 무료) | ✅ | ✅ 최고 품질 |

선택 근거:
- Groq는 토큰 생성 속도가 200~300 tokens/sec으로 현존 가장 빠른 무료 LLM 추론 서비스다. 단, 30 RPM(분당 30회) 제한으로 동시 통화 30콜 이상 부하에서는 유료 전환 필요.
- Gemini 2.0 Flash는 한국어 자연스러움과 문맥 이해도가 가장 우수하며, 1M 토큰 컨텍스트로 장시간 세션에도 안정적이다. AI Studio 무료 한도(1,500 req/일) 초과 시 Google AI Platform 유료 전환 필요.

#### TTS (Text-to-Speech)

| 구분 | 벤더 | 서비스 | 무료 한도 | 스트리밍 | 한국어 |
|---|---|---|---|---|---|
| Primary | **Google** | Cloud TTS (Neural2 / WaveNet) | 100만 자/월 | ✅ | ✅ 최고 품질 |
| Fallback | **Microsoft** | Edge TTS (비공식 무료 API) | 무제한 | ✅ | ✅ SunHiNeural 등 |

선택 근거:
- Google Cloud TTS는 한국어 Neural2 음성 품질이 가장 자연스러우며 스트리밍 지원이 안정적이다.
- Edge TTS는 완전 무료·무제한이며 `ko-KR-SunHiNeural` 등 고품질 한국어 음성을 제공해 fallback으로 적합하다.

---

### 6.3 벤더 구성 요약

```
STT  : Groq Whisper (Primary)  →  Google Cloud STT (Fallback)
LLM  : Groq Llama 4 (Primary)  →  Gemini 2.0 Flash (Fallback)
TTS  : Google Cloud TTS (Primary)  →  Edge TTS (Fallback)
VBGW : 자체 커스텀 개발 (AgentOE 전용)
```

이 구성은 아키텍처 설계서 v2.0의 다음 항목과 직접 연결된다.

- `6.5 Speech Orchestrator` — STT/TTS capability abstraction 계층에 각 벤더를 등록
- `6.8 Tool Router / MCP Broker` — capability_descriptor에 벤더별 streaming_support, latency_class 반영
- `11.1 Degraded Mode Matrix` — STT/LLM/TTS 장애 시 자동 fallback 경로로 위 Fallback 벤더 사용
- `8.2 인터페이스 버전 관리` — 각 벤더 API 버전을 connector_version으로 관리

---

### 6.4 벤더 Capability Descriptor 초안

각 벤더는 아래 형식으로 Connector Registry에 등록한다.

**Groq STT (Whisper)**
```yaml
connector_id: groq-whisper-v1
connector_version: "1.0"
capability_descriptor:
  streaming_support: true
  partial_result: false        # final result only
  language_support: [ko, en, ja, zh]
  latency_class: ultra-low
  custom_phrase_biasing: false
  diarization: false
timeout_policy: { request_ms: 3000, streaming_ms: 30000 }
retry_policy: { max_retries: 2, backoff_ms: 200 }
degraded_mode_support: true
fallback_connector: google-stt-v1
```

**Google Cloud STT**
```yaml
connector_id: google-stt-v1
connector_version: "1.0"
capability_descriptor:
  streaming_support: true
  partial_result: true
  language_support: [ko, en, ja, zh, ...]
  latency_class: low
  custom_phrase_biasing: true
  diarization: true
timeout_policy: { request_ms: 5000, streaming_ms: 60000 }
retry_policy: { max_retries: 3, backoff_ms: 500 }
degraded_mode_support: true
fallback_connector: null      # 최종 fallback
```

**Groq LLM (Llama 4)**
```yaml
connector_id: groq-llama4-v1
connector_version: "1.0"
capability_descriptor:
  streaming_support: true
  context_window: 131072
  latency_class: ultra-low
  function_calling: true
  json_mode: true
timeout_policy: { request_ms: 8000, streaming_ms: 30000 }
retry_policy: { max_retries: 2, backoff_ms: 300 }
degraded_mode_support: true
fallback_connector: gemini-flash-v1
```

**Google Gemini 2.0 Flash**
```yaml
connector_id: gemini-flash-v1
connector_version: "1.0"
capability_descriptor:
  streaming_support: true
  context_window: 1048576      # 1M tokens
  latency_class: low
  function_calling: true
  json_mode: true
timeout_policy: { request_ms: 10000, streaming_ms: 60000 }
retry_policy: { max_retries: 3, backoff_ms: 500 }
degraded_mode_support: true
fallback_connector: null
```

**Google Cloud TTS**
```yaml
connector_id: google-tts-v1
connector_version: "1.0"
capability_descriptor:
  streaming_support: true
  voice_catalog: [ko-KR-Neural2-A, ko-KR-Neural2-B, ko-KR-Wavenet-A, ...]
  latency_class: low
  ssml_support: true
  custom_speed: true
timeout_policy: { request_ms: 3000, streaming_ms: 15000 }
retry_policy: { max_retries: 2, backoff_ms: 200 }
degraded_mode_support: true
fallback_connector: edge-tts-v1
```

**Microsoft Edge TTS**
```yaml
connector_id: edge-tts-v1
connector_version: "1.0"
capability_descriptor:
  streaming_support: true
  voice_catalog: [ko-KR-SunHiNeural, ko-KR-InJoonNeural]
  latency_class: medium
  ssml_support: true
  custom_speed: true
timeout_policy: { request_ms: 5000, streaming_ms: 20000 }
retry_policy: { max_retries: 3, backoff_ms: 500 }
degraded_mode_support: true
fallback_connector: null
```
