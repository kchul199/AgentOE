# Monorepo 통합 + Phase T/N (운영포탈 · SSE · KMS · SIPp)

## 개요
monorepo 통합 베이스 위에 Phase T(로컬 전수 테스트) + Phase N(통합 운영포탈) 전체를 반영한다. 단일 squash 금지 — vbgw_v2 subtree 이력 보존을 위해 **merge commit**으로 머지한다.

## 주요 변경

### 운영포탈 (Phase N)
- backend: `auth_portal`(bcrypt+TOTP MFA+refresh rotation+CSRF), `stream`(SSE 4채널), `audit_emitter`(WORM Time Series + Redis pub), `sse_broadcaster`, `portal_cookie_middleware`, `workers/am_poller`(leader election), `infra/{kms,prometheus,alertmanager}_client`
- `services/ops-portal`(React/Vite SPA), `services/ops-api`(데모 백엔드) 신규
- `deploy/helm/agentoe-portal` + staging/prod values + `network-policy-backend`
- mongo: `migrate_phase_n_audit`, `migrate_phase_n_portal_users`
- scripts: `portal_staging_deploy.sh`, `portal_prod_deploy.sh`, `seed_portal_admin.py`, `migrate_mfa_to_kms.py`
- `.github/workflows/portal-build.yml`

### 테스트/부하 인프라 (Phase T/T+)
- backend integration/unit/performance 테스트 (portal auth/RBAC/audit, SLO compliance)
- freeswitch SIPp: `uac_10cps_audio.xml`, `run_10cps_10min.sh`, `parse_sipp_results.py`, `compose.sipp.yml`

### 문서 (산출물)
- `docs/business/`: 프로그램 명세서, 인터페이스 정의서, DB 명세서(xlsx) + 시스템 아키텍처 설계서(docx)
- `docs/guide/phase-N-ops-portal-plan.md`, `docs/runbook/portal-{staging,prod}-deploy.md`, `docs/TEST_PLAN*.md`

### 버그 수정
- `scenarios.py`: `Request` import 누락 수정 (F821 — publish/delete 엔드포인트 NameError)

## 검증
- [x] GitHub Actions YAML 파싱
- [x] Python 구문 컴파일
- [x] ruff check/format (수정 후 clean)
- [ ] go build 3종 (host)
- [ ] mypy (host)
- [ ] helm lint matrix (host)
- [ ] proto contracts-gen drift (host)

## 머지 방식
⚠️ **Create a merge commit** — squash/rebase 금지 (subtree 이력 보존)

## 후속
머지 후 `vbgw_v2` archive, `docs/HANDOFF.md` §3/§6 갱신, 이후 운영포탈 staging 실배포(`portal_staging_deploy.sh`).
