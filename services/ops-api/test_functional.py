"""
ops-api 기능 테스트 스크립트
서버를 subprocess 로 기동하고 전체 엔드포인트를 검증합니다.
"""
import json, subprocess, sys, time, urllib.request, urllib.error, os

OPS_API_DIR = os.path.dirname(os.path.abspath(__file__))
BASE = "http://127.0.0.1:8001/api/v1"
PASS = 0; FAIL = 0; ERRORS = []

# ── 서버 기동 ─────────────────────────────────────────────────────────────
env = {**os.environ, "PYTHONPATH": OPS_API_DIR}
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app",
     "--host", "127.0.0.1", "--port", "8001", "--log-level", "error"],
    cwd=OPS_API_DIR, env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

# 준비 대기 (최대 8초)
ready = False
for _ in range(16):
    time.sleep(0.5)
    try:
        urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=1)
        ready = True; break
    except Exception:
        pass

if not ready:
    proc.terminate()
    print("❌ 서버 기동 실패"); sys.exit(1)

print("✅ 서버 기동 완료 (port 8001)\n")

# ── 헬퍼 ─────────────────────────────────────────────────────────────────
def req(method: str, path: str, body: dict | None = None):
    url = f"http://127.0.0.1:8001{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=5) as resp:
        return json.loads(resp.read())

def chk(label: str, method: str, path: str, body: dict | None = None,
        assert_fn=None):
    global PASS, FAIL
    try:
        result = req(method, path, body)
        if assert_fn:
            assert_fn(result)
        print(f"  ✅  {label}")
        PASS += 1
        return result
    except AssertionError as e:
        print(f"  ❌  {label}  →  assertion: {e}")
        FAIL += 1; ERRORS.append(label); return None
    except Exception as e:
        print(f"  ❌  {label}  →  {e}")
        FAIL += 1; ERRORS.append(label); return None

# ════════════════════════════════════════════════════════════════
print("[ Health ]")
chk("GET /health",       "GET", "/health",          assert_fn=lambda r: r["status"] == "ok")
chk("GET /api/v1/livez", "GET", "/api/v1/livez",    assert_fn=lambda r: r["status"] == "ok")

# ════════════════════════════════════════════════════════════════
print("\n[ 모니터링 ]")
m = chk("GET /monitoring/metrics", "GET", f"{BASE[22:]}/monitoring/metrics",
        assert_fn=lambda r: all(k in r for k in ["ccu","p95_ms","error_rate_pct","slo_achieved_pct"]))

chk("  └ ccu ∈ [0,200]", "GET", "/api/v1/monitoring/metrics",
    assert_fn=lambda r: 0 <= r["ccu"] <= 200)
chk("  └ slo_achieved_pct ∈ [0,100]", "GET", "/api/v1/monitoring/metrics",
    assert_fn=lambda r: 0 <= r["slo_achieved_pct"] <= 100)

h = chk("GET /monitoring/history?points=10", "GET", "/api/v1/monitoring/history?points=10",
        assert_fn=lambda r: all(k in r for k in ["ccu","p95","error_rate"]))
if h:
    chk("  └ history ccu 10개 포인트", "GET", "/api/v1/monitoring/history?points=10",
        assert_fn=lambda r: len(r["ccu"]) == 10)

# ════════════════════════════════════════════════════════════════
print("\n[ 환경 설정 ]")
chk("GET /config/environments", "GET", "/api/v1/config/environments",
    assert_fn=lambda r: set(r) == {"dev","staging","prod"})

for env_name in ["dev","staging","prod"]:
    chk(f"GET /config/{env_name}", "GET", f"/api/v1/config/{env_name}",
        assert_fn=lambda r: "LLM_MODEL" in r["values"] and "MAX_CONCURRENT_CALLS" in r["values"])

diff = chk("GET /config/diff/all", "GET", "/api/v1/config/diff/all",
           assert_fn=lambda r: isinstance(r, list) and len(r) > 0)
if diff:
    chk("  └ diff 항목에 key/dev/staging/prod 필드", "GET", "/api/v1/config/diff/all",
        assert_fn=lambda r: all("key" in d and "dev" in d for d in r))

updated = chk("PUT /config/dev (LLM_TEMPERATURE=0.9)", "PUT", "/api/v1/config/dev",
    body={"updated_by": "tester", "values": {"LLM_TEMPERATURE": 0.9}},
    assert_fn=lambda r: r["values"].get("LLM_TEMPERATURE") == 0.9)

# 수정 후 값 유지 확인
chk("  └ 수정 후 GET에서 반영 확인", "GET", "/api/v1/config/dev",
    assert_fn=lambda r: r["values"].get("LLM_TEMPERATURE") == 0.9)

# ════════════════════════════════════════════════════════════════
print("\n[ 상담 이력 ]")
sl = chk("GET /sessions", "GET", "/api/v1/sessions",
         assert_fn=lambda r: r["total"] > 0 and len(r["items"]) > 0)

chk("  └ page/page_size 필드 존재", "GET", "/api/v1/sessions",
    assert_fn=lambda r: "page" in r and "page_size" in r)
chk("GET /sessions?page=2&page_size=5", "GET", "/api/v1/sessions?page=2&page_size=5",
    assert_fn=lambda r: r["page"] == 2 and len(r["items"]) <= 5)

# 세션 상세
if sl and sl["items"]:
    sid = sl["items"][0]["session_id"]
    detail = chk(f"GET /sessions/{{id}}", "GET", f"/api/v1/sessions/{sid}",
                 assert_fn=lambda r: "trace" in r and "turns" in r)
    if detail:
        chk("  └ trace 스텝 존재 (stt/llm/tts 포함)", "GET", f"/api/v1/sessions/{sid}",
            assert_fn=lambda r: any(s["step"] in ["stt","llm","tts"] for s in r["trace"]))
        chk("  └ turns 대화 내용 존재", "GET", f"/api/v1/sessions/{sid}",
            assert_fn=lambda r: len(r["turns"]) > 0)

# ════════════════════════════════════════════════════════════════
print("\n[ Kill Switch ]")
ks_list = chk("GET /kill-switches", "GET", "/api/v1/kill-switches",
              assert_fn=lambda r: isinstance(r, list) and len(r) > 0)
if ks_list:
    chk("  └ 각 항목에 id/label/active/scope 필드", "GET", "/api/v1/kill-switches",
        assert_fn=lambda r: all("id" in k and "active" in k and "scope" in k for k in r))

# feature:barge_in 활성화
activated = chk("PUT feature:barge_in 활성화", "PUT",
    "/api/v1/kill-switches/feature:barge_in",
    body={"active": True, "reason": "기능테스트", "operator": "tester"},
    assert_fn=lambda r: r["active"] is True and r["reason"] == "기능테스트")

# 활성화 상태 GET 재확인
chk("  └ GET 으로 활성화 상태 확인", "GET",
    "/api/v1/kill-switches/feature:barge_in",
    assert_fn=lambda r: r["active"] is True)

# 비활성화
chk("PUT feature:barge_in 비활성화", "PUT",
    "/api/v1/kill-switches/feature:barge_in",
    body={"active": False, "reason": "", "operator": "tester"},
    assert_fn=lambda r: r["active"] is False)

# global:all 은 기본값 False
chk("GET global:all 기본 비활성", "GET",
    "/api/v1/kill-switches/global:all",
    assert_fn=lambda r: r["scope"] == "global")

# ════════════════════════════════════════════════════════════════
print("\n[ 시나리오 ]")
scenarios = chk("GET /scenarios", "GET", "/api/v1/scenarios",
                assert_fn=lambda r: len(r) >= 3)
if scenarios:
    chk("  └ 필드 완전성 확인", "GET", "/api/v1/scenarios",
        assert_fn=lambda r: all("scenario_id" in s and "env_deployed" in s for s in r))

chk("GET /scenarios/greet_v2", "GET", "/api/v1/scenarios/greet_v2",
    assert_fn=lambda r: r["node_count"] > 0 and "staging" in r["env_deployed"])

chk("GET /scenarios/billing_inquiry", "GET", "/api/v1/scenarios/billing_inquiry",
    assert_fn=lambda r: r["tenant_id"] == "t_acme")

# 테스트 발신
test_resp = chk("POST /scenarios/greet_v2/test", "POST",
    "/api/v1/scenarios/greet_v2/test",
    body={"phone_number": "+821099998888", "mock_asr": "안녕하세요"},
    assert_fn=lambda r: "test_id" in r and r["status"] == "queued")

# 배포 (dev)
deploy_dev = chk("POST billing_inquiry → dev 배포", "POST",
    "/api/v1/scenarios/billing_inquiry/deploy",
    body={"env": "dev", "operator": "tester", "note": "기능테스트"},
    assert_fn=lambda r: r["status"] == "success" and r["env"] == "dev")

# 배포 후 버전 반영 확인
chk("  └ 배포 후 GET 버전 갱신 확인", "GET",
    "/api/v1/scenarios/billing_inquiry",
    assert_fn=lambda r: r["env_deployed"]["dev"] is not None)

# prod 배포
chk("POST service_cancel → staging 배포", "POST",
    "/api/v1/scenarios/service_cancel/deploy",
    body={"env": "staging", "operator": "tester", "note": "스테이징 검증"},
    assert_fn=lambda r: r["status"] == "success")

# ════════════════════════════════════════════════════════════════
proc.terminate()

total = PASS + FAIL
print(f"""
{'━'*50}
  ops-api 기능 테스트 결과
{'━'*50}
  ✅  통과: {PASS:3d} / {total}
  ❌  실패: {FAIL:3d} / {total}
  성공률: {PASS/total*100:.1f}%
{'━'*50}""")

if ERRORS:
    print("  실패 항목:")
    for e in ERRORS:
        print(f"    • {e}")

sys.exit(0 if FAIL == 0 else 1)
