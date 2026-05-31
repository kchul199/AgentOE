# Phase N — 통합 운영포탈 (Operations Portal) Plan v2

> **목적**: agentoe 운영자가 **모니터링 / 환경설정 / 상담이력 / 로그트레이스** 및 추가 운영 기능을 한 곳에서 처리할 수 있는 웹 포탈 구축.
>
> **위치**: `services/portal/` (신규 React SPA 서비스, 별도 Helm 차트 + 별도 컨테이너, internal ALB)
>
> **상태**: 2026-05-12 kick-off · 2026-05-12 v2 revision (검증 에이전트의 G1-G7/R1-R9 반영).
>
> **버전 히스토리**:
> - v1 (2026-05-12 초안): 4영역 MVP + 추가기능 A-H + sub-phase N1-N5.
> - v2 (2026-05-12 revision): 운영자 인증 흐름, audit schema 확장, SSE fan-out, AM proxy, ALB/uvicorn 한계, CORS, host/cert 정합성을 본문에 박음. N1 task 8-10 → 12-15 로 확장. portal 자체 SLO/알람 추가.
> - **v2.1 (2026-05-12 NG closing)**: NG1 audit_events TS 호환 (metadata.* 인덱스만 추가) + NG2 RBAC issuer 격리 (자동 매핑 폐기 + require_portal_role) + NG3 backend NetworkPolicy ingress 강화 (portal namespace 만). N1 12 sub-task 의존성 순서 박음.

## 0. 절대 규칙 (CLAUDE.md 적용)

| 규칙             | 운영포탈 적용                                                                |
|------------------|-------------------------------------------------------------------------------|
| Performance First | SSE 핸들러는 async generator. polling 금지. backend block 금지.              |
| Latency is King   | SPA initial chunk ≤ 350KB gzip (recharts 동적 import 제외). Page LCP ≤ 1.5s (LAN). SSE first-byte ≤ 200ms. |
| Error Handling    | SSE 끊김 시 자동 재연결 (exponential backoff). 끊긴 동안 KPI 카드는 stale 표식 + 마지막 timestamp 유지. backend 단일 실패점 부재 (Redis/Mongo 둘 중 하나 다운돼도 부분 동작). |

## 1. 스코프

### 1.1 MVP (N1~N3)

| 영역            | 화면                          | 데이터 소스                                                |
|-----------------|-------------------------------|------------------------------------------------------------|
| 모니터링        | 채널/시나리오 실시간 대시보드 | `/api/v1/metrics/*`, `/stream/metrics` (Prom poll), `/stream/sessions.active` (Redis pub) |
| 환경설정        | dev/staging/prod 전환 + config view | env switcher (cookie + 헤더), `/api/v1/admin/*`, `/api/v1/kill_switch`, Helm values (read-only via gitops API) |
| 상담 이력       | Session 검색 + 리플레이 (text turn timeline) | `/api/v1/sessions`, `/api/v1/sessions/{id}/turns` (신규)  |
| 로그 트레이스   | audit log + trace ID drill-down | `/api/v1/audit`, `/stream/audit.tail` (Redis pub), trace_id query → backend audit only (vbgw cross-service drill은 R2 결정 후) |

### 1.2 추가 운영 기능 (N4)

| 코드 | 기능                       | 가치                                  | backend 작업 규모              |
|------|----------------------------|---------------------------------------|--------------------------------|
| A    | Tenant 관리                | 신규 테넌트 등록 + quota + API key 발급 | M (DB 스키마 + admin endpoint) |
| B    | 시나리오 배포 관리         | draft → staging → prod 승급 + rollback | L (scenarios 확장 + 승격 워크플로 + audit) |
| C    | 활성 알람 뷰 / 알람 라우팅 | Alertmanager firing + silence 컨트롤  | M (AM proxy + silence audit)   |
| D    | Connector 헬스             | Groq/OpenAI/STT/TTS provider 상태     | M (provider별 health checker + cache) |
| E    | 운영자 계정 관리           | 운영자 CRUD + role + audit            | M (portal_users CRUD UI + audit) |
| F    | 인시던트 타임라인          | alert → ack → resolved + postmortem 헬퍼 | L (AM history join + audit + 템플릿) |
| G    | Canary 배포 컨트롤         | Phase Z canary block 슬라이더 + abort | XL (backend → kubectl/helm via in-cluster SA + ClusterRole + audit + rollback) |
| H    | Kill-switch 빠른 토글      | 큰 버튼 + scope별 confirm + audit     | S (이미 router 있음, UI만)     |

### 1.3 비스코프 (의도적 제외)

- 시나리오 빌더 (기존 `services/frontend`).
- Grafana 대체 — 포탈은 KPI/상태 뷰. 깊은 분석은 Grafana deep-link.
- 로그 풀텍스트 검색 (Loki/ES 위임).
- 알람 룰 편집 (GitOps, read-only).
- **Audio replay** — 통화 녹음은 vbgw 측 저장. 포탈은 turn-level 텍스트만.

## 2. 아키텍처

### 2.1 디렉토리

```
services/portal/                          ★ 신규
├── Dockerfile                            (multi-stage: node build → nginx serve)
├── nginx.conf                            (gzip, SPA fallback, /api proxy, SSE buffering off)
├── package.json                          (Vite + React + TS + Tailwind + react-router + @tanstack/react-query + recharts + lucide-react + zod)
├── vite.config.ts
├── tsconfig.json
├── openapi.config.ts                     (openapi-typescript codegen 설정 — R6 해결)
├── index.html
├── public/                               (favicon, manifest)
├── src/
│   ├── main.tsx                          (router + QueryClient + AuthProvider + EnvProvider + SSEProvider)
│   ├── App.tsx                           (RootLayout)
│   ├── lib/
│   │   ├── api.ts                        (fetch w/ CSRF + env header + 401 redirect)
│   │   ├── sse.ts                        (SSEClient w/ exponential backoff + lastEventId + stale 표식)
│   │   ├── auth.ts                       (login + MFA challenge + refresh + 로그아웃)
│   │   ├── env.ts                        (env switcher: cookie persist + 헤더 + prod 가드)
│   │   └── csrf.ts                       (double-submit token 추출/주입)
│   ├── generated/                        (openapi-typescript 산출물 — git ignore)
│   ├── providers/
│   │   ├── AuthProvider.tsx              (JWT in HttpOnly cookie, refresh 사이클)
│   │   ├── EnvProvider.tsx
│   │   └── ThemeProvider.tsx
│   ├── components/
│   │   ├── layout/{Sidebar,Topbar,EnvBadge,ProdBanner,UserMenu}.tsx
│   │   ├── kpi/{KpiCard,Sparkline,StalenessIndicator,SseStatusBadge}.tsx
│   │   ├── table/{DataTable,CursorPager,RowActions}.tsx
│   │   ├── filters/{DateRange,TenantPicker,ScenarioPicker,TraceIdInput}.tsx
│   │   ├── modal/{ConfirmDialog,KillSwitchDialog,SilenceDialog}.tsx
│   │   └── trace/{TraceTimeline,SpanCard,TurnDetail}.tsx
│   ├── pages/
│   │   ├── monitoring/{ChannelsPage,ScenariosPage,LiveSessionsPage}.tsx
│   │   ├── settings/{EnvConfigPage,FeatureFlagsPage,KillSwitchPage}.tsx
│   │   ├── sessions/{SessionSearchPage,SessionReplayPage}.tsx
│   │   ├── trace/{AuditPage,TraceDetailPage}.tsx
│   │   ├── ops/{TenantsPage,ConnectorsHealthPage,AlertsPage,CanaryPage,UsersPage,IncidentTimelinePage}.tsx
│   │   └── auth/{LoginPage,MfaChallengePage,MfaEnrollPage,PasswordChangePage}.tsx
│   ├── types/                            (수기 미러 X — generated/ 재export 만)
│   └── styles/{app.css,tokens.css}
└── tests/
    ├── unit/ (Vitest)
    └── e2e/  (Playwright — W-11~W-30, 기존 e2e 디렉토리와 분리)
```

### 2.2 backend 변경점 (services/backend/app/api/v1/routers/ 및 도메인)

| 분류            | 경로                                              | 역할                              | RBAC                       |
|-----------------|---------------------------------------------------|-----------------------------------|----------------------------|
| **신규 `auth_portal.py`** | `POST /api/v1/auth/portal/login`         | username/password → MFA challenge token (5분) | 인증 전                     |
|                 | `POST /api/v1/auth/portal/mfa/verify`             | TOTP 검증 → access(15분) + refresh(8h) JWT, HttpOnly cookie + CSRF | challenge token            |
|                 | `POST /api/v1/auth/portal/refresh`                | refresh token rotation            | refresh 쿠키               |
|                 | `POST /api/v1/auth/portal/logout`                 | 양쪽 쿠키 삭제 + refresh 무효화   | 인증                       |
|                 | `POST /api/v1/auth/portal/mfa/enroll`             | TOTP secret 발급 (QR)             | portal:viewer+             |
| **신규 `stream.py`**     | `GET /api/v1/stream/metrics`              | Prometheus poll → KPI tick 1s SSE | portal:viewer+             |
|                 | `GET /api/v1/stream/sessions.active`              | Redis pub `agentoe:events:sessions` → SSE | portal:viewer+         |
|                 | `GET /api/v1/stream/audit.tail`                   | Redis pub `agentoe:events:audit` → SSE | portal:operator+      |
|                 | `GET /api/v1/stream/alerts`                       | AM API poll + Redis pub fan-out → SSE | portal:viewer+         |
| **확장 `sessions.py`**   | `GET /api/v1/sessions/{id}/turns`         | turn-by-turn 리플레이 (text)      | portal:viewer+ (tenant 일치) |
| **확장 `audit.py`**      | `GET /api/v1/audit?trace_id=&actor=&action=` | trace_id 인덱스 사용 + actor 필터 | portal:operator+        |
| **확장 `admin.py`**      | `GET /api/v1/admin/env/info`              | env 식별 + git sha + 빌드시점     | portal:viewer+             |
|                 | `GET /api/v1/admin/feature-flags`                 | flag list                         | portal:viewer+             |
|                 | `POST /api/v1/admin/feature-flags/{key}`          | flag toggle (audit 기록)          | portal:admin               |
|                 | `GET /api/v1/admin/alerts` (AM proxy)             | 현재 firing 목록                  | portal:viewer+             |
|                 | `POST /api/v1/admin/alerts/silence`               | silence 생성 (audit 기록)         | portal:operator+           |
|                 | `DELETE /api/v1/admin/alerts/silence/{id}`        | silence 해제 (audit 기록)         | portal:operator+           |
| **신규 `portal_users.py`** | CRUD `/api/v1/portal/users/*`           | 운영자 계정 관리                  | portal:admin               |
| **신규 `ops/*.py` (N4)** | tenants / connectors / canary / incident-timeline | 추가 운영 기능 (사용자 결정 시)   | portal:operator/admin      |
| **신규 도메인**          | `app/domain/audit_emitter.py`             | audit emit + Redis pub 동시 (fan-out 책임) | -                  |
|                 | `app/domain/portal_session.py`                    | refresh token rotation + 동시 세션 제한 | -                |
|                 | `app/infra/alertmanager_client.py`                | AM API 클라이언트 + silence 위임  | -                          |

### 2.3 RBAC 정책 (기존 `core/auth.py` 호환성 매트릭스 — G1/NG2 해결)

#### ★ v2.1 핵심 변경 (NG2 closing)
**자동 role 매핑 제거**. 기존 admin/super_admin 토큰이 portal:* 권한을 자동 획득하면 portal 격리가 깨지므로, **portal:\* role 은 오직 portal_users 컬렉션에서 발급된 토큰에만** 박힘. 기존 role 매핑은 **명시적 grant** 가 아니면 무효.

#### 토큰 issuer 기반 격리

JWT 발급 시 `iss` claim 으로 출처 식별:

| Issuer             | 발급 경로                                | 받을 수 있는 role                              |
|--------------------|------------------------------------------|------------------------------------------------|
| `iss="agentoe-api"` | 기존 `/api/v1/auth/token`                | `operator`, `admin`, `super_admin`, `platform_admin`, `sre_admin` (기존 그대로) |
| `iss="agentoe-portal"` | 신규 `/api/v1/auth/portal/mfa/verify` | `portal:viewer`, `portal:operator`, `portal:admin` (portal_users.portal_roles 기준) |

`portal:*` role 은 portal issuer 토큰에만 박힘. agentoe-api issuer 토큰의 roles 배열에 누군가 수동으로 `portal:admin` 을 박더라도 portal-protected 라우터는 issuer 검증으로 거부.

#### 매트릭스 (단순화 — 자동 부여 표 폐기)

| 기존 role        | portal:* 권한 자동 부여 | portal 접근 가능 조건 |
|------------------|--------------------------|--------------------------|
| `platform_admin` | ❌                        | 별도 portal_users 등록 필요 (단, `assert_tenant_ownership` 우회는 그대로) |
| `sre_admin`      | ❌                        | 동일 |
| `super_admin`    | ❌                        | 동일 |
| `admin`          | ❌                        | 동일 |
| `operator`       | ❌                        | 동일 |

#### 신규 portal-only role

| Role             | 권한                                                                  |
|------------------|------------------------------------------------------------------------|
| `portal:viewer`  | 모니터링, 상담이력 read, 환경설정 view, 알람 view                       |
| `portal:operator`| viewer + kill-switch 토글, audit drill, silence, 시나리오 승급          |
| `portal:admin`   | operator + feature flag, 운영자 계정 관리, 알람 라우팅, 운영자 CRUD     |

#### 구현 디테일

- **issuer 검증**: `app/core/auth.py` 의 `_decode_with_jwks` / `_decode_legacy_hs` 결과 직후 `payload["iss"]` 추출 → `TenantContext.issuer` 신규 필드 (str). 기본값 `"agentoe-api"`.
- **portal route 보호**: 신규 `require_portal_role(*roles)` dependency — `TenantContext.issuer == "agentoe-portal"` 강제 + role 검증. `platform_admin` 자동 통과는 **issuer 가 portal 인 경우에 한해** 유지 (portal_users 에 platform_admin 도 있을 수 있음 — drop-in 호환).
- **PLATFORM_ADMIN_ROLES 확장**:
  ```python
  PLATFORM_ADMIN_ROLES = frozenset({
      "platform_admin", "sre_admin", "super_admin",  # 기존 + 신규 super_admin
      "portal:admin",                                # 신규
  })
  ```
- **`assert_tenant_ownership` portal 우회 분기**: `portal:admin` 도 cross-tenant 우회 허용 (감사로그 기록 — 기존 `platform_admin` 패턴 답습).
- **기존 라우터 `require_roles(...)` 는 변경 없음**. portal 의 모든 데이터 접근은 신규 `require_portal_role()` 을 거치며, 내부적으로 backend 서비스 메서드를 호출하는 식. 즉 router 레이어 OR 확장 불필요 — portal-protected wrapper endpoint 가 별도 신설되거나, 라우터에 issuer 분기 dependency 가 추가됨.
- **routers/kill_switch.py 처리 예시**:
  - 기존: `require_roles("super_admin","admin")` 유지
  - 신규: portal 에서 호출하는 경로는 `routers/portal_kill_switch.py` (신규 wrapper) — `require_portal_role("portal:operator","portal:admin")` 데코레이터, 내부에서 기존 `kill_switch_service.toggle()` 도메인 호출. audit emit 은 wrapper 가 책임.

### 2.4 SSE event bus / fan-out (G4 해결)

#### 채널 source 매트릭스

| SSE 경로                    | Source                                | 이유                          |
|-----------------------------|---------------------------------------|-------------------------------|
| `/stream/metrics`           | Prometheus query API (poll 1s)        | 단일 source, cluster-wide 정확 |
| `/stream/sessions.active`   | Redis pub `agentoe:events:sessions`   | 다중 pod 의 이벤트 fan-out     |
| `/stream/audit.tail`        | Redis pub `agentoe:events:audit`      | 동일                          |
| `/stream/alerts`            | AM API poll (10s) → Redis pub `agentoe:events:alerts` → SSE | AM 단일 호출 분배 |

#### Publisher 책임

- `app/domain/audit_emitter.py` — `await emit(action, actor, before, after, trace_id, env, ...)` 호출 시 (1) Mongo audit_events insert, (2) Redis publish `agentoe:events:audit` 동시. Mongo 실패시 Redis 만, Redis 실패 시 Mongo 만 (graceful degradation).
- Session lifecycle hook — sessions 컬렉션 update 시 `session.start/end/error` 이벤트를 Redis publish.
- Alertmanager poller — `app/workers/am_poller.py` (FastAPI lifespan task) 10s 주기로 AM `/api/v2/alerts` 호출 → 변화분만 Redis publish.

#### Subscriber (SSE handler)

- pod 별 Redis subscriber 1개 (asyncio task) → in-process broadcaster → 해당 pod 에 연결된 모든 SSE 클라이언트로 fan-out.
- pod 가 N개여도 Redis pub/sub 가 N개 pod 에 동시 전달 → 모든 운영자가 동일 이벤트 수신.

#### SSE event schema (canonical)

```jsonc
// /api/v1/stream/metrics — every 1s tick (source: Prometheus)
event: metrics.tick
data: {
  "ts": "2026-05-12T...",
  "env": "staging",
  "active_sessions": 42,
  "cps_1m": 12.3,
  "p95_ms_5m": 287,
  "success_rate_5m": 0.998,
  "per_scenario": [{"scenario_id":"s1","calls":100,"p95":250,"success":0.999}, ...]
    // 정렬: CPS desc (호출량 많은 시나리오 우선), 상위 20 + __other__
}

// /api/v1/stream/sessions.active (source: Redis pub)
event: session.start | session.end | session.error
data: { "session_id":"...", "tenant_id":"...", "scenario_id":"...", "channel":"...", "ts":"..." }

// /api/v1/stream/audit.tail (source: Redis pub)
event: audit.append
data: {
  "ts":"...", "env":"prod",
  "actor": {"tenant_id":"...", "client_id":"...", "roles":["portal:operator"], "ip":"...", "user_agent":"..."},
  "action": "kill_switch.toggle",
  "trace_id":"...",
  "before": {...}, "after": {...}
}

// /api/v1/stream/alerts (source: AM poll → Redis pub)
event: alert.firing | alert.resolved | alert.silenced
data: { "alertname":"...", "severity":"page|warn|info", "labels":{...}, "starts_at":"...", "annotations":{...} }

// 공통 — backend 15s, nginx X-Accel-Buffering: no
event: heartbeat
data: { "ts":"..." }
```

### 2.5 env switcher 보안 모델 (R3 보완)

- **단일 SPA 빌드 + 런타임 config.json**: portal 자체는 **internal ALB (VPN/IP allowlist)** 로 한정. `config.json` 도 internal 에서만 GET 가능.
- env 마다 backend cluster 도메인은 환경변수 (`PORTAL_API_DEV/STAGING/PROD`).
- 운영자가 env 라디오로 선택 → `X-Env-Target` 헤더 + 쿠키 persist (24h).
- **env 위변조 차단**: backend 가 자기 cluster 의 env (`settings.ENV`) 와 헤더 불일치 시 즉시 4xx + audit emit ("env_header_mismatch"). 헤더는 정보용일 뿐, 실제 라우팅은 cluster 도메인.
- **prod 가드** (R4 보완 — scope 별 분기):
  - env=prod 진입 시 topbar 빨간 띠 + "PRODUCTION" 배지 + 자동 logout 60분.
  - kill_switch scope 별 confirm:
    - `tenant` scope → tenant_id 직접 타이핑
    - `feature` scope → feature_name 직접 타이핑
    - `scenario` scope → scenario_id 직접 타이핑
  - 모든 prod write 액션은 ConfirmDialog 2단계 + 5초 카운트다운 + abort 버튼.
- **JWT 보관 위치 (R-nit 해결)**: HttpOnly cookie (XSS 시 도용 차단) + CSRF double-submit token (`X-CSRF-Token` 헤더 + `__csrf__` 쿠키 매칭). localStorage 금지.

### 2.6 audit schema v2 + 호출 사이트 (G3/NG1 해결)

#### ★ v2.1 핵심 변경 (NG1 closing)

`audit_events` 는 **MongoDB Time Series 컬렉션** (`mongo/init_schema.js:127` `timeseries: {timeField, metaField, granularity}`). TS 컬렉션 제약:
1. individual document update/delete 불가 (전체 컬렉션 drop/recreate 또는 backfill 만).
2. metaField (= `metadata`) 외 필드 인덱스 비효율.
3. schema validator 미지원.

→ v2 의 "기존 `actor: str` → `actor.client_id` 이동" 은 **실행 불가**. v2.1 은 **append-only + metaField 확장** 패턴 채택.

#### 신규 스키마 (TS 호환)

기존 필드는 **그대로 유지**. 신규 필드는 모두 `metadata` 하위에 추가:

```jsonc
// audit_events (v2.1, Time Series 호환)
{
  "_id": ObjectId,
  "timestamp": ISODate,                       // timeField (기존)
  "actor": "<client_id 그대로 — backward compat>",  // 기존 str 유지
  "details": { ... },                          // 기존 dict
  "metadata": {                                // metaField — 모든 신규 필드 여기
    "tenant_id": "...",                        // 기존
    "session_id": "...",                       // 기존
    "event_type": "...",                       // 기존
    // ── 이하 신규 (v2.1) ──
    "env": "dev|staging|prod",
    "actor_client_id": "...",                  // = actor 와 동일 값 복사 (인덱스용)
    "actor_roles": ["portal:operator"],
    "actor_ip": "10.0.0.1",
    "actor_user_agent": "Mozilla/...",
    "actor_issuer": "agentoe-portal",
    "action": "kill_switch.toggle",
    "trace_id": "...",
    "resource_type": "kill_switch",
    "resource_id": "feature:foo",
    "before": {...},
    "after": {...}
  }
}
```

이유:
- TS 컬렉션은 metaField 안의 필드에 secondary index 가 효율적.
- 기존 `actor: str`, `details: dict` 를 건드리지 않아 backward compat — 기존 query 코드 (`audit_repository.py:80-100` 의 read path) 변경 불필요.
- 신규 query (trace_id drill, actor.client_id 검색) 는 `metadata.trace_id`, `metadata.actor_client_id` 인덱스로 처리.

#### 마이그레이션 (N1 산출물) — **drop/recreate 없음**

- `mongo/migrate_phase_n_audit.js` — TS 콜렉션 schema 변경 없음. **인덱스 추가만**:
  ```js
  db.audit_events.createIndex({ "metadata.trace_id": 1, "timestamp": -1 });
  db.audit_events.createIndex({ "metadata.actor_client_id": 1, "timestamp": -1 });
  db.audit_events.createIndex({ "metadata.action": 1, "timestamp": -1 });
  db.audit_events.createIndex({ "metadata.env": 1, "timestamp": -1 });
  ```
- 기존 데이터 backfill 불필요 — 옛 도큐먼트는 `metadata.trace_id == null` 이지만 그게 정상 (그때는 추적 안 됐음). 신규 emit 만 채움.
- env 별 TTL 분기는 기존 `expireAfterSeconds: 94608000` (3년) 을 env 별 cluster init script 에서 override:
  - prod: 31_536_000 (1년)
  - staging: 7_776_000 (90일)
  - dev: 2_592_000 (30일)
  → `mongo/init_schema_{dev,staging,prod}.js` 신규 (deploy/k8s-bootstrap 또는 helm hook). 기존 `init_schema.js` 는 base.
- 보존 정책 단일 진실: `docs/reference/data-retention.md` (신규). CLAUDE.md / HANDOFF.md 에서 참조.

#### audit emit 호출 사이트 추가 (N1)

`AuditRepository.log()` 가 현재 0회 호출 — N1 에 emit 추가할 사이트:
- `kill_switch.py` activate/deactivate
- `admin.py` 의 tenant CRUD, feature-flag toggle
- `scenarios.py` 의 publish/delete/promote
- `auth_portal.py` login_success/login_fail/mfa_fail/logout/password_change
- `connectors.py` write 경로 (있다면)
- 신규 portal wrapper 라우터 (§2.3 의 `portal_kill_switch.py` 등)

#### audit_emitter 통합 헬퍼

```python
# app/domain/audit_emitter.py
async def emit(
    *, action: str, actor: TenantContext, resource: dict | None = None,
    before: dict | None = None, after: dict | None = None,
    request: Request | None = None,
) -> None:
    """Mongo TS insert + Redis publish 동시 (graceful degradation)."""
    event = build_audit_event(action, actor, resource, before, after, request)
    # 1) Mongo insert (실패 시 Redis 만)
    try: await audit_repo.insert(event)
    except Exception as e: logger.error("audit_mongo_failed", ...)
    # 2) Redis pub (실패 시 Mongo 만)
    try: await redis.publish("agentoe:events:audit", json.dumps(event))
    except Exception as e: logger.error("audit_redis_failed", ...)
```

호출 패턴: 라우터에서 `Depends(get_audit_emitter)` 로 주입 — module-level import 대신 (테스트 용이).

### 2.7 운영자 인증 / 토큰 발급 (G2 해결)

#### 데이터 모델

```jsonc
// portal_users (신규 컬렉션)
{
  "_id": ObjectId,
  "username": "charls@agentoe.io",
  "password_hash": "$2b$12$...",            // bcrypt cost 12
  "mfa_secret": "<encrypted with KMS>",      // TOTP base32, AES-GCM with envelope key
  "mfa_enrolled": true,
  "portal_roles": ["portal:operator"],       // 또는 ["portal:admin"]
  "legacy_roles": [],                        // 기존 admin/super_admin 호환 필요시
  "tenant_scope": null,                      // null = 모든 tenant, 또는 ["tenant_a"]
  "active": true,
  "created_at": ISODate,
  "last_login_at": ISODate,
  "failed_attempts": 0,
  "locked_until": null                       // brute force 잠금
}
```

#### 로그인 흐름

```
POST /auth/portal/login (username, password)
  └─ bcrypt verify → 실패 시 failed_attempts++, 5회 5분 잠금
  └─ 성공: challenge_token (5분 TTL, in-memory or Redis) 발급
  └─ audit: portal.login.password_verified

POST /auth/portal/mfa/verify (challenge_token, totp_code)
  └─ TOTP verify (window=1)
  └─ 성공: access JWT (15분) + refresh JWT (8시간, rotation) 발급
  └─ HttpOnly Secure SameSite=Strict 쿠키로 응답 set
  └─ CSRF token (random 32바이트) → __csrf__ 쿠키 + 응답 body
  └─ audit: portal.login.success

POST /auth/portal/refresh (refresh 쿠키)
  └─ refresh 검증 + rotation (옛 refresh 무효화)
  └─ 새 access + 새 refresh
  └─ audit: portal.token.refresh

POST /auth/portal/logout
  └─ refresh 무효화 (Redis denylist 8h)
  └─ 쿠키 삭제
```

#### MFA 등록 (최초)

- `POST /auth/portal/mfa/enroll` → TOTP secret 발급 + QR 코드 (Google Authenticator/1Password 호환).
- 미등록 사용자는 로그인 후 `MfaEnrollPage` 강제 redirect.
- 백업 코드 8개 발급 (한 번 표시, 이후 해시만 저장).

#### 동시 세션 / 잠금 정책

- 같은 사용자 동시 5개 refresh token 허용. 초과 시 가장 오래된 것부터 만료.
- 실패 5회 → 5분 잠금. 10회 → 30분. 20회 → 24h.
- prod env 선택 시 60분 절대 만료 (refresh 무효).

### 2.8 Alertmanager proxy / silence (G5/NG3 해결)

#### ★ v2.1 핵심 변경 (NG3 closing)

AM 자체에 basic auth 없음 + backend 가 권한의 단일 진실. 따라서 **backend Service 의 NetworkPolicy ingress 가 portal Pod 만 허용** 해야 silence 권한이 다른 namespace 로 누수되지 않음.

#### 네트워크 — egress (backend → AM)

- backend Pod 의 NetworkPolicy egress 에 `monitoring` namespace 의 alertmanager Service 허용 추가 (`deploy/helm/agentoe-backend/templates/networkpolicy.yaml` 확장).
- AM endpoint: `http://kube-prometheus-stack-alertmanager.monitoring.svc.cluster.local:9093` (cluster-internal).

#### 네트워크 — ingress (portal → backend) **★ NG3 신규**

- `deploy/helm/agentoe-backend/templates/networkpolicy.yaml` 의 ingress 룰 강화:
  ```yaml
  ingress:
    # 기존 — public 라우터 (sessions / scenarios / pipelines 등)
    - from:
        - namespaceSelector:
            matchLabels: { kubernetes.io/metadata.name: "ingress-nginx" }   # or aws-alb-ctrl
      ports:
        - port: 8000
      # portal-protected route 가 아닌 일반 API 만 — 단, NetworkPolicy 는 L7 분기 불가하므로
      # portal-protected route 보호는 backend application 레이어 (issuer 검증) 가 책임 (§2.3 NG2)
    # 신규 — portal-protected route (silence 등) 는 portal Pod 만 호출 가능
    # 이건 NetworkPolicy 가 path-level 분기 못하므로, backend application 이 issuer 검증으로
    # 보장. NetworkPolicy 는 추가 방어선 — portal namespace 도 허용 명시:
    - from:
        - namespaceSelector:
            matchLabels: { kubernetes.io/metadata.name: "agentoe-portal" }
          podSelector:
            matchLabels: { app: "agentoe-portal" }
      ports:
        - port: 8000
  ```
- portal namespace 라벨링: Helm 의 `deploy/helm/agentoe-portal/templates/namespace.yaml` 신규 — `kubernetes.io/metadata.name: agentoe-portal[-staging]` (K8s 1.22+ 자동 주입이지만 명시적 라벨).
- **AM basic auth** (단일 진실 강화) — N1 산출물 **포함** (TODO 에서 격상):
  - kube-prometheus-stack values 의 `alertmanager.alertmanagerSpec.web.tlsConfig` + basic auth user/pass (External Secrets Operator 로 주입).
  - backend `alertmanager_client.py` 가 basic auth header 자동 첨부.
  - 운영자 → portal → backend (portal-issuer 검증) → AM (basic auth) 의 4중 권한 사슬.

#### API 위임

- `GET /api/v1/admin/alerts` → AM `/api/v2/alerts?active=true` proxy + 응답 정규화.
- `POST /api/v1/admin/alerts/silence` → AM `/api/v2/silences` proxy + audit emit ("alert.silence.create").
- `DELETE /api/v1/admin/alerts/silence/{id}` → AM `/api/v2/silence/{id}` proxy + audit emit.
- silence 권한은 `portal:operator` 이상 + `iss="agentoe-portal"` 강제 (`require_portal_role()` 데코레이터, §2.3).

### 2.9 Helm / 배포 토폴로지 (R8 해결)

- 차트: `deploy/helm/agentoe-portal/` (기존 agentoe-frontend chart 구조 복제 + internal ALB 설정).
- host: `ops.{env}.agentoe.io` (예: `ops.staging.agentoe.io`, `ops.agentoe.io`).
- ALB:
  - **internal scheme** (`alb.ingress.kubernetes.io/scheme: internal`)
  - VPN/IP allowlist 권장 — `alb.ingress.kubernetes.io/security-groups: <vpn-sg>`
  - idle timeout: `alb.ingress.kubernetes.io/load-balancer-attributes: idle_timeout.timeout_seconds=600` (SSE 안정성)
  - group: 신규 `agentoe-internal` (frontend public group 과 분리)
  - group.order: 300
- TLS: ACM cert SAN 에 `ops.staging.agentoe.io`, `ops.agentoe.io` 추가 (Terraform `aws_acm_certificate` 의 `subject_alternative_names` 확장).
- nginx config:
  - `proxy_buffering off`
  - `proxy_read_timeout 1h`
  - `X-Accel-Buffering: no` (SSE 경로만)
  - gzip on, gzip_types text/css application/javascript application/json image/svg+xml
  - SPA fallback (`try_files $uri $uri/ /index.html`)
- ConfigMap 패턴: `agentoe-portal-runtime-env` 의 `env.js` 가 envsubst 로 빌드 → nginx static serve. (기존 frontend chart 의 `configmap.yaml` 패턴 답습)

### 2.10 portal 자체 SLO / 알람 (R5 해결)

`docs/reference/slo.md` 에 다음 행 추가:

| 서비스   | 약속                                              | 목표  | 측정 윈도우 |
|----------|---------------------------------------------------|-------|-------------|
| `portal` | `/portal/*` HTTP 요청 성공률 (5xx ≠ 클라이언트 오류) | 99.9% | 30일 rolling |
| `portal` | 페이지 LCP P95 ≤ 1.5s (synthetic, Lighthouse)      | 95%   | 7일 rolling |
| `portal` | SSE first-byte ≤ 200ms                            | 99%   | 30일 rolling |
| `portal` | SSE 재연결 중 끊김 < 5분/일                        | -     | 일별        |

신규 PrometheusRule: `deploy/k8s-bootstrap/manifests/prometheus-rules/portal-slo.yaml` (15-20 rule).

Alertmanager 라우팅: portal 다운 = `#ops-platform` (P3), SSE first-byte degradation = `#ops-alerts` (P4).

### 2.11 CORS / 신규 헤더 (G7 해결)

`services/backend/app/main.py:102-108` 의 CORSMiddleware 수정:

```python
CORSMiddleware(
  allow_origins=settings.CORS_ORIGINS,    # ops.{env}.agentoe.io 추가
  allow_credentials=True,                  # HttpOnly cookie 위해 필수
  allow_methods=["GET","POST","PUT","DELETE","PATCH"],
  allow_headers=[
    "Authorization", "X-Tenant-Id", "X-Trace-Id",    # 기존
    "X-Env-Target", "X-Portal-Action", "X-CSRF-Token", # 신규
    "Last-Event-ID",                                   # SSE 재연결
    "Content-Type",
  ],
  expose_headers=["X-Trace-Id","X-Request-ID"],
)
```

### 2.12 ALB / uvicorn / HPA 한계 (G6 해결)

#### uvicorn 설정

- `--limit-concurrency 1000` (pod 당 max 1000 connection — SSE 4채널 × 운영자 200명 여유)
- `--timeout-keep-alive 75` (ALB idle 60s 보다 길게)
- `--workers 2` (현행 유지)

#### ALB

- idle timeout 600s annotation (위 §2.9 참조)
- target group health check 가 SSE 가 아닌 `/healthz` 만 (현행 유지)
- HTTP/2 활성 (alb 기본)

#### HPA custom metric

- 기존 CPU 70% 트리거는 idle SSE 로 트리거 안 됨.
- 신규 metric `portal_sse_active_connections` (gauge) 노출 — `/metrics/prometheus` 에 포함.
- HPA customMetric: `portal_sse_active_connections` > 800/pod 시 scale-out.

#### SSE connection 가드

- pod 당 800 connection 초과 시 신규 SSE 요청 503 + `Retry-After: 30`.

### 2.13 데이터 인덱스 / 페이지네이션 (R1 해결)

#### sessions 인덱스 추가

- 신규: `tenant_id_1_created_at_-1` compound index (cursor pagination 효율)
- `mongo/migrate_phase_n_indexes.js` 신규.

#### sessions list API

- `GET /api/v1/sessions?after=<created_at>&limit=50` cursor-based (offset 폐기, 1000건 이후 P95 안정).
- 기존 endpoint 도 호환 유지 (offset 모드도 받되 deprecated 표식).

#### turns API

- `GET /api/v1/sessions/{id}/turns?after=<turn_index>&limit=100`
- session_history 컬렉션의 `session_id+turn_index` 인덱스 활용.
- tenant ownership 검증은 sessions 조회 1회 + cache (60s).

### 2.14 openapi-typescript codegen (R6 해결)

- `services/portal/package.json` 에 `"openapi:gen": "openapi-typescript http://backend:8000/openapi.json -o src/generated/api.d.ts"` 스크립트.
- CI lint job: backend 기동 → openapi.json fetch → codegen → `git diff --exit-code src/generated/`. drift 시 fail.
- 수기 미러 (types/api.ts) 폐지, generated/ 에서 re-export.

### 2.15 CI / ECR / OIDC (R7 해결)

- `.github/workflows/build-images.yml` services 리스트에 `portal` 추가 (matrix include).
- `.github/workflows/validate.yml` 의 helm-lint matrix 에 `agentoe-portal` 추가.
- Terraform `deploy/terraform/envs/{staging,prod}/main.tf` 에 `aws_ecr_repository.agentoe-portal` 추가 (immutable tag).
- `deploy/terraform/modules/github-oidc` 의 ECR push 정책 ARN allowlist 에 portal repo 추가.
- `services/portal/**` path filter 4개 workflow 에 추가.

## 3. Sub-phase 분해 (v2.1)

| Phase | 산출물                                                                                                            | 예상 task |
|-------|-------------------------------------------------------------------------------------------------------------------|-----------|
| N0 ★  | 이 plan 문서 (v2.1 — NG1/NG2/NG3 closing 포함)                                                                     | 1         |
| N1    | 아래 의존성 순서로 12 sub-task (감독: §3.1)                                                                       | **12**    |
| N2    | 모니터링 (채널/시나리오/활성세션) + 환경설정 (env switcher/SLO 뷰/scope별 kill-switch dialog/feature flag)         | 7-9       |
| N3    | 상담 이력 (검색/cursor pager/리플레이) + 로그 트레이스 (audit 페이지/trace_id drill — backend audit 한정)        | 6-8       |
| N4    | 추가 운영 기능 — N1 끝나는 시점 AskUserQuestion 으로 4-6개 선정                                                    | 8-12      |
| N5    | Dockerfile + Helm 차트 (internal ALB) + Terraform (ECR + ACM SAN) + GHA workflow + Playwright E2E (W-11~W-30) + portal SLO 추가 + HANDOFF.md 갱신 + data-retention.md 신규 | 8-10 |

총 41-50 task.

### 3.1 N1 의존성 순서 (★ 검증 에이전트 권고 반영)

각 sub-task 는 이전 task 의존. 순서대로 진행 — 중간 끊기면 다음 task blocked.

| # | sub-task                                                            | 의존성       |
|---|---------------------------------------------------------------------|--------------|
| **N1.1** | audit schema v2.1 (TS 호환 metadata.* 인덱스) + portal_users 컬렉션 idempotent createCollection | -            |
| **N1.2** | RBAC: TenantContext.issuer 필드 + expand_roles 폐기 + require_portal_role() + assert_tenant_ownership portal:admin 우회 + PLATFORM_ADMIN_ROLES 확장 | N1.1         |
| **N1.3** | audit_emitter 헬퍼 (Mongo + Redis 동시 + graceful degradation) + emit 호출 사이트 5곳 (kill_switch / admin / scenarios / auth_portal / connectors) | N1.1, N1.2   |
| **N1.4** | Redis pub/sub 인프라 — publisher util + lifespan subscriber asyncio task + in-process broadcaster + leader election (K8s lease 또는 Redis SETNX) | N1.3         |
| **N1.5** | SSE 4채널 핸들러 (`/stream/metrics`, `/stream/sessions.active`, `/stream/audit.tail`, `/stream/alerts`) + heartbeat 15s + Last-Event-ID catch-up (audit/alerts 만) | N1.4         |
| **N1.6** | AM proxy 라우터 (`/admin/alerts*`) + alertmanager_client.py + AM basic auth 적용 + backend NetworkPolicy 강화 (ingress portal namespace 만 + egress monitoring NS) | N1.5         |
| **N1.7** | 운영자 인증 (`/auth/portal/login` → `/mfa/verify` → access/refresh JWT, HttpOnly+CSRF) + portal_users CRUD + bcrypt + TOTP MFA + 잠금 정책 + refresh denylist Redis | N1.2, N1.3   |
| **N1.8** | sessions cursor pagination + `tenant_id+created_at` 인덱스 + `/sessions/{id}/turns` API (session_history 진실) | N1.1         |
| **N1.9** | CORS 확장 (allow_credentials + X-Env-Target / X-Portal-Action / X-CSRF-Token / Last-Event-ID) + settings.CORS_ORIGINS 에 ops.{env}.agentoe.io 추가 | -            |
| **N1.10** | backend `app/scripts/dump_openapi.py` (FastAPI app.openapi() 직접 호출 — lifespan 우회) + CI lint job 추가 | -            |
| **N1.11** | services/portal Vite scaffold + AuthProvider + EnvProvider + SSEProvider + LoginPage + MfaChallengePage + 1 placeholder dashboard | N1.7, N1.9   |
| **N1.12** | uvicorn limit-concurrency 1000 + ALB idle 600s annotation + SSE pod당 800 conn 가드 + portal_sse_active_connections gauge (Prometheus-Adapter 의존성은 N5 로 deferred — HPA 는 일단 CPU 만)| N1.5         |

핵심 critical path: **N1.1 → N1.2 → N1.3 → N1.4 → N1.5 → N1.6**. 병렬 가능: N1.7 (N1.2/N1.3 후), N1.8/N1.9/N1.10 (독립), N1.11 (N1.7/N1.9 후), N1.12 (N1.5 후).

## 4. 다음 단계 후보 (N1 진입 결정용)

| 후보                    | 무엇                                                          |
|-------------------------|---------------------------------------------------------------|
| **N1 즉시 시작 (추천)** | 12-15 task 분해 → TaskCreate → 진행                            |
| MVP 스코프 축소         | N1 의 (g) 운영자 인증을 v1 (단순 password+JWT) 으로 줄이고 MFA 는 N5 |
| 추가 운영기능 우선순위 재조정 | A-H 중 H (kill-switch UI) 만 N2 와 병행                       |

내 추천: **N1 즉시 시작 (전부 풀스택)**.

## 5. 함정 / 결정사항 (확장)

### 5.1 SSE 정합성
- Redis 단일 실패점 — Redis 다운 시 SSE 전 채널 빈 화면 위험. backend 가 Redis 재연결 + 그동안은 in-process broadcaster 만 (pod-local). UI 는 stale 표식.
- Redis pub/sub 는 at-most-once — 재연결 동안 미스 이벤트는 영구 손실. audit 은 Mongo 가 진실 (재조회 가능), session/alert 는 다음 tick 에서 self-correct.

### 5.2 prod 안전장치
- env=prod write 액션 ConfirmDialog 2단계 + scope별 직접 타이핑 + 5초 카운트다운 + abort. (R4)
- prod 진입 60분 자동 logout.
- prod 의 audit log retention 1년 (`data-retention.md`).

### 5.3 카디널리티 / 비용
- Prometheus per_scenario top-20 + __other__. 정렬 기준: CPS desc.
- MongoDB audit_events 1년 보존 + `trace_id` 단일 인덱스 → 인덱스 크기 모니터링 (Phase 3-B alertmanager 규칙 추가).
- session list cursor pagination — 1000건 이후 P95 안정.

### 5.4 인증 / 보안
- JWT 보관: HttpOnly + Secure + SameSite=Strict 쿠키.
- CSRF: double-submit token (`X-CSRF-Token` 헤더 + `__csrf__` 쿠키).
- 운영자 brute force: 5회 5분 잠금 / 10회 30분 / 20회 24h.
- portal 자체 internal ALB (VPN/IP allowlist). public 노출 금지.
- env 별 토큰 분리: 토큰 발급은 cluster 별. dev 토큰으로 prod 요청 → JWKS 키 불일치 → 4xx + audit.

### 5.5 ALB / uvicorn
- ALB idle 600s + uvicorn `--limit-concurrency 1000` + SSE keep-alive 75s.
- pod 당 SSE 800 conn 가드, 초과 시 503 + Retry-After.
- HPA customMetric `portal_sse_active_connections` 추가.

### 5.6 CORS / 헤더
- CORS allow_credentials=True, allow_headers 에 `X-Env-Target`, `X-Portal-Action`, `X-CSRF-Token`, `Last-Event-ID` 추가.

### 5.7 helm/배포
- portal host: `ops.{env}.agentoe.io`, internal ALB, group `agentoe-internal`, order 300.
- ACM cert SAN 확장.
- `config.json` (런타임 env) 은 internal 에서만 GET.

### 5.8 audit emit 책임
- **explicit 호출** 패턴 (router 책임). 미들웨어 자동 emit X — before/after 누락 위험.
- 공통 헬퍼: `audit_emitter.emit(action, actor, resource, before, after, trace_id, env, request)`.
- request 객체에서 ip/user_agent 자동 추출.

### 5.9 트레이스 cross-service 정합성 (R2)
- N3 시작 전 vbgw-bridge/orchestrator 의 trace_id 전파 정합성 확인.
- 안 되면 portal trace drill 은 backend audit 한정으로 명시 (스코프 축소). 이후 follow-up phase.

### 5.10 generated 코드 drift
- openapi-typescript CI 검증 (R6).

## 6. 검증 기준 (확장)

### N1 검증 (12-15 task 완료 시)
- 4개 SSE 채널이 mock client 로 30초 안정 수신 + 강제 끊김 후 자동 재연결 + Redis 끊김 후 backend 가 in-process fallback 모드 동작.
- RBAC unit test: **5 role × 5 endpoint** (portal:viewer/operator/admin + super_admin + operator [거부 확인]).
- audit emit 통합 test: kill_switch.toggle 1회 → Mongo + Redis 동시 기록 + SSE 클라이언트 수신 확인.
- 운영자 로그인 흐름 E2E: password → MFA → access cookie + CSRF + refresh rotation + logout.
- AM silence E2E: backend → AM cluster-internal 호출 + audit.

### N2 검증
- Playwright smoke: 모니터링 페이지 KPI 카드 4개 표시 + env switcher 작동 + kill_switch scope별 dialog (3 scope).
- Lighthouse LCP ≤ 1.5s (initial chunk 350KB gzip 검증).

### N3 검증
- Session 1000건 cursor pagination P95 ≤ 500ms (실측, `slo.md` 의 backend P95 와 일치).
- trace_id query 1회 → backend audit 1건 이상 표시.

### N4 검증
- 선정 기능별 별도.

### N5 검증
- Helm lint × 2 env (staging/prod) PASS.
- Docker multi-stage build < 200MB.
- E2E 풀스택 (W-11~W-30) PASS.
- portal SLO 4개 행 `slo.md` 에 반영 + PrometheusRule lint PASS.
- HANDOFF.md §3/§6 갱신.
- data-retention.md 신규.

## 7. 참조

- 기존 frontend 빌더: `services/frontend/`
- 기존 RBAC: `services/backend/app/core/auth.py`
- 기존 예외 체계: `services/backend/app/core/exceptions.py` (AuthenticationError / AuthorizationError)
- 기존 SLO: `docs/reference/slo.md` (portal 행 추가 예정)
- 기존 audit_repository: `services/backend/app/repositories/audit_repository.py`
- 기존 audit 스키마: `mongo/init_schema.js:125-150` (마이그레이션 대상)
- 기존 E2E 인프라: `services/frontend/tests/e2e/` (참고용, portal 은 별도)
- 기존 frontend Helm: `deploy/helm/agentoe-frontend/` (구조 복제)
- 기존 alertmanager values: `deploy/helm/observability/` 또는 k8s-bootstrap manifests
- CI workflow: `.github/workflows/{build-images,validate,deploy-staging,deploy-prod,security-scan}.yml`
- 신규 산출 문서: `docs/reference/data-retention.md` (N5 산출물)

---

## 8.1 v2.1 revision 변경점 (v2 대비 — NG1/NG2/NG3 closing)

| NG ID | v2.1 변경 |
|-------|-----------|
| **NG1** | §2.6 audit_events 가 MongoDB Time Series 콜렉션 — in-place migration 불가. 신규 필드를 모두 `metadata.*` (metaField) 하위로 두고 기존 `actor:str`, `details:dict` 유지. migration 은 인덱스 추가만, schema/데이터 변경 없음 |
| **NG2** | §2.3 RBAC 자동 매핑 매트릭스 폐기. JWT `iss` claim 으로 issuer 격리 (`agentoe-api` vs `agentoe-portal`). portal:* role 은 portal_users 발급 토큰에만. expand_roles() 폐기. require_portal_role() 데코레이터 신규 (issuer + role 검증). assert_tenant_ownership 의 portal:admin 우회 분기 추가 |
| **NG3** | §2.8 backend NetworkPolicy ingress 강화 — portal namespace (matchLabels app=agentoe-portal) 만 허용 룰 추가. AM basic auth 적용을 N1 산출물에 포함 (TODO 격상). portal Helm 의 namespace 라벨 명시 |

## 8. v2 revision 적용 변경점 요약 (v1 대비)

| ID  | 변경 내용 |
|-----|-----------|
| G1  | §2.3 RBAC 매핑 매트릭스 + `PLATFORM_ADMIN_ROLES` 확장 + 기존 라우터 require_roles OR 확장 |
| G2  | §2.7 운영자 인증 (MongoDB users + bcrypt + TOTP MFA + refresh rotation) 전면 신규 |
| G3  | §2.6 audit schema v2 + 마이그레이션 + emit 호출 사이트 명시 + retention env 별 분기 |
| G4  | §2.4 SSE event bus = Redis pub/sub (audit/sessions) + Prometheus poll (metrics) + AM poll→Redis (alerts), source 컬럼 명시 |
| G5  | §2.2 + §2.8 AM proxy endpoint 신규 + NetworkPolicy egress |
| G6  | §2.12 uvicorn limit-concurrency + ALB idle 600s + HPA customMetric + pod당 SSE 800 conn 가드 |
| G7  | §2.11 CORS allow_headers 확장 (X-Env-Target/X-Portal-Action/X-CSRF-Token/Last-Event-ID) + allow_credentials |
| R1  | §2.13 sessions cursor pagination + 신규 인덱스 |
| R3  | §2.9 portal internal ALB + IP allowlist + config.json internal-only |
| R4  | §5.2 prod confirm 가드 scope 별 분기 |
| R5  | §2.10 portal SLO 4행 추가 + PrometheusRule 신규 |
| R6  | §2.14 openapi-typescript codegen + CI drift 검증 |
| R7  | §2.15 ECR repo + OIDC role policy + GHA path-filter 추가 |
| R8  | §2.9 portal host (ops.{env}.agentoe.io) + group + cert SAN |
| Nit | Phase N1 task 갯수 8-10 → 12-15, RBAC 검증 4→5 role, observability.md 신규(N5), JWT 보관 HttpOnly+CSRF, kill_switch scope 별 가드 분기 |

> 이 문서는 Phase N 진행 동안 업데이트 (§3 의 phase 완료 시 ★ 마킹).
