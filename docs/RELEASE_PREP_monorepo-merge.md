# feat/monorepo-merge — Push · PR · Merge 실행 가이드

> 작성: 2026-05-23 · 대상 브랜치 `feat/monorepo-merge` · repo `kchul199/AgentOE`
> 본 문서는 sandbox에서 실행 불가한 작업(push/PR/merge/archive)을 **사용자 host에서 직접** 수행하기 위한 검증 결과 + 명령 세트다.

---

## 0. 가장 중요한 발견 (먼저 읽을 것)

검토 결과, 단순한 "210개 사소한 변경"이 아니다. **커밋된 브랜치(`origin/feat/monorepo-merge` = `c866ab0`)는 monorepo 통합 시점에 멈춰 있고, 이후의 Phase T + Phase N(운영포탈) 작업 전체가 working tree에 미커밋 상태**다.

확인 사실:

- 커밋된 트리에 `services/ops-portal`, `services/ops-api`, `auth_portal.py`, `stream.py` 등 Phase N 산출물이 **없음** (미커밋).
- working tree = 131 modified + 79 untracked = 210 변경 (5,014 insertions / 1,827 deletions).
- 따라서 그냥 push해도 **Phase N이 올라가지 않는다** — 먼저 커밋해야 한다.

### ⚠️ 보안 — 커밋하면 안 되는 파일

| 파일 | 문제 | 조치 |
|---|---|---|
| `mongo/mongo-keyfile` | MongoDB replica set keyfile (1024B base64 시크릿). `.gitignore` 미적용, untracked. | **절대 커밋 금지** — `.gitignore`에 추가 |

`.env.ops-portal.example`은 dev/dummy 값(`agentoe_dev_pass`, `dev-jwt-secret-local-only`)만 있어 `.example` 파일로 커밋 가능.

### 커밋에서 제외할 노이즈 (현재 `.gitignore` 미적용)

`load_test_*.log`(10), `test_results/`, `services/freeswitch/results/`, `services/backend/.coverage.*`, `services/frontend/*.timestamp-*.mjs`(8) — 빌드/테스트 아티팩트.

---

## 1. 사전 정리 — .gitignore 보강 + 시크릿 제외

```bash
cd ~/AgenticOE_v2

> ✅ **이미 적용 완료** (working tree). `.gitignore` 에 아래가 추가되었고 `git check-ignore` 로 6개 패턴 모두 IGNORED 확인됨. gitignore 는 인라인 `#` 주석을 지원하지 않으므로 주석은 별도 줄로 작성한다.

```gitignore
# ── release prep 보강 (2026-05-23) ──
# MongoDB keyfile (시크릿 — 절대 커밋 금지)
mongo/mongo-keyfile
# 부하/테스트 아티팩트
/load_test_*.log
/test_results/
services/freeswitch/results/
services/backend/.coverage*
# Vite/Vitest 임시 빌드 산출물
services/frontend/*.timestamp-*.mjs
```

---

## 2. CI green 만들기

> sandbox에서 `services/backend` 기준 CI 검사를 미리 돌리고 **lint/format/버그를 이미 수정 완료**했다. 현재 working tree 기준 결과:

| 검사 | 결과 | 비고 |
|---|---|---|
| GitHub Actions YAML 파싱 | ✅ PASS | 전체 워크플로 |
| Python 구문 컴파일 (`py_compile`) | ✅ PASS | backend/ops-api/scripts |
| `ruff check app/ tests/` | ✅ **PASS** | 초기 245건 → 0건 (자동수정 139+49, 정책 보강, 개별 수정 18) |
| `ruff format --check app/ tests/` | ✅ **PASS** | 전 파일 포맷 완료 |
| **F821 (Request import 버그)** | ✅ **수정됨** | `scenarios.py` import 추가 |
| mypy / go build / helm lint / proto drift / frontend tsc | ⚠️ 미검증 | sandbox에 도구 없음 — **host에서 확인 필요(2-3)** |

### 2-1~2-2. 이미 적용된 수정 (working tree)

- **버그**: `scenarios.py` 의 `from fastapi import ... Request ...` 추가 (publish/delete 엔드포인트 NameError 해소).
- **자동수정**: `ruff check --fix`(139) + `--unsafe-fixes`(49) + `ruff format`.
- **정책 보강** (`services/backend/pyproject.toml`): 전역 ignore 에 `RUF001/002/003`(한글 ambiguous-unicode), `RUF012`(클래스 상수 컬렉션) 추가. per-file-ignore 에 tests `E402/B017/RUF005/RUF043/SIM117`, 라우터 2종 `E402` 추가.
- **개별 수정**(18): `B904`(raise … from), `S324`(usedforsecurity=False), `S311`(noqa), `S110/S112/SIM105`(contextlib.suppress), `SIM102/SIM108`(조건 결합·삼항), `UP007`(noqa).

> 재확인: `cd ~/AgenticOE_v2/services/backend && ruff check app/ tests/ && ruff format --check app/ tests/` → 둘 다 통과해야 한다.

### 2-3. host에서만 가능한 검사 (반드시 수행)

```bash
cd ~/AgenticOE_v2
# Go 3종 빌드
for m in services/vbgw-ai services/vbgw-bridge services/vbgw-orchestrator; do (cd "$m" && go build ./...) || echo "FAIL $m"; done
# mypy
cd services/backend && mypy app/ --ignore-missing-imports; cd ../..
# helm lint (차트 × env)
cd deploy/helm && ENV=staging make lint; cd ../..
# proto drift (수정 시)
cd contracts && make gen && git diff --exit-code gen/ ; cd ..
```

---

## 3. 커밋 & push

```bash
cd ~/AgenticOE_v2
git checkout feat/monorepo-merge
git status                          # 시크릿/노이즈가 더 이상 안 보이는지 재확인

git add -A
git status                          # mongo-keyfile 등 제외 확인 (★ 필수)

git commit -m "feat: Phase T/N 통합 — 운영포탈(ops-portal/ops-api), SSE 4채널, KMS MFA, SIPp 부하 인프라, 산출물 문서

- backend: auth_portal/stream/audit_emitter/sse_broadcaster/portal_cookie_mw, infra(kms/prometheus/alertmanager), workers(am_poller)
- services/ops-portal, services/ops-api 신규
- deploy/helm/agentoe-portal + portal values + network-policy
- mongo: phase_n 마이그레이션, scripts: portal deploy/seed/migrate_mfa
- docs: 프로그램명세서/인터페이스정의서/DB명세서/아키텍처설계서 + 운영포탈 plan/runbook
- fix: scenarios.py Request import (F821)"

git push origin feat/monorepo-merge
```

---

## 4. PR open & merge (★ squash/rebase 금지)

```bash
# gh CLI 사용 시
gh pr create --base main --head feat/monorepo-merge \
  --title "Monorepo 통합 + Phase T/N (운영포탈·SSE·KMS·SIPp)" \
  --body-file docs/PR_BODY_monorepo-merge.md

# CI green 확인 후 — merge commit 으로만 (subtree 이력 보존)
gh pr merge --merge          # ❌ --squash / --rebase 사용 금지
```

웹 UI로 할 경우: PR 생성 → Checks 전부 green 확인 → **"Create a merge commit"** 선택 (Squash/Rebase 금지).

> 사유: vbgw_v2의 34 commits를 subtree로 보존했으므로 squash/rebase 시 이력이 소실된다.

---

## 5. 머지 후 — vbgw_v2 archive

```bash
# 로컬 lock 정리
cd ~/vbgw_v2 && rm -f .git/index.lock
```

GitHub UI → `kchul199/vbgw_v2` → **Settings → Archive this repository** (이력 보존, 새 PR/issue 차단).

(선택) repo rename `AgentOE` → `agentoe`:
```bash
git remote set-url origin git@github.com:kchul199/agentoe.git
```

---

## 6. 체크리스트

```
□ .gitignore 보강 + mongo-keyfile 제외 확인 (git check-ignore)
□ scenarios.py Request import 수정 (F821)
□ ruff check --fix + ruff format → ruff check 재실행 clean
□ (host) go build 3종 / mypy / helm lint / proto drift PASS
□ git add -A 후 git status 에 시크릿/노이즈 없음 확인
□ commit (Phase T/N 통합 메시지)
□ push origin feat/monorepo-merge
□ PR open → CI 전부 green
□ merge commit 으로 머지 (squash/rebase 금지)
□ vbgw_v2 archive
□ docs/HANDOFF.md §3/§6 갱신
```
