// migrate_phase_n_audit.js — Phase N (운영포탈) audit_events 인덱스 확장
//
// 적용 시점: Phase N1.1 — 운영포탈 plan v2.1 의 NG1 closing 산출물.
//
// 핵심:
//   - audit_events 는 MongoDB Time Series 콜렉션 (init_schema.js:127).
//   - TS 콜렉션은 in-place document update 불가, schema validator 미지원, metaField 외 인덱스 비효율.
//   - 따라서 신규 필드는 모두 metadata.* (metaField) 하위로 추가하고, 인덱스만 추가한다.
//   - 기존 데이터/스키마/TTL 은 변경 없음 — append-only, backward-compatible.
//
// 신규 필드 (운영포탈에서 emit 시 채움 — 옛 레코드는 null 정상):
//   metadata.env                  — "dev|staging|prod"
//   metadata.actor_client_id      — JWT.sub (인덱스용 — actor:str 과 동일 값)
//   metadata.actor_roles          — list[str]
//   metadata.actor_ip             — str
//   metadata.actor_user_agent     — str
//   metadata.actor_issuer         — "agentoe-api|agentoe-portal"
//   metadata.action               — "kill_switch.toggle|alert.silence.create|..."
//   metadata.trace_id             — W3C traceparent 의 trace-id 부
//   metadata.resource_type        — "kill_switch|tenant|scenario|feature_flag|..."
//   metadata.resource_id          — str
//   metadata.before / metadata.after — dict snapshot (write 액션 한정)
//
// Idempotent — 여러 번 실행해도 안전 (createIndex 가 동일 spec 이면 noop).
//
// 사용:
//   mongosh "$MONGODB_URI" --quiet mongo/migrate_phase_n_audit.js

print("=== Phase N — audit_events 인덱스 확장 시작 ===");

if (!db.getCollectionNames().includes("audit_events")) {
  print("✗ audit_events 컬렉션이 없음. init_schema.js 를 먼저 실행하세요.");
  quit(1);
}

const collInfo = db.getCollectionInfos({ name: "audit_events" })[0];
if (!collInfo.options || !collInfo.options.timeseries) {
  print("✗ audit_events 가 Time Series 컬렉션이 아님. 마이그레이션 중단.");
  quit(1);
}
print("✓ audit_events Time Series 확인 — metaField=" + collInfo.options.timeseries.metaField);

// --- 신규 인덱스 4개 (운영포탈 query path) ----------------------------------------

// 1) trace_id drill: trace_id 로 audit 이벤트 일렬 추적
db.audit_events.createIndex(
  { "metadata.trace_id": 1, "timestamp": -1 },
  { name: "trace_id_ts_desc", sparse: true }
);
print("  + idx: trace_id_ts_desc (sparse)");

// 2) actor 검색: 누가 언제 무엇을 했는지
db.audit_events.createIndex(
  { "metadata.actor_client_id": 1, "timestamp": -1 },
  { name: "actor_client_id_ts_desc", sparse: true }
);
print("  + idx: actor_client_id_ts_desc (sparse)");

// 3) action 별 추출: kill_switch.toggle 만 보기 등
db.audit_events.createIndex(
  { "metadata.action": 1, "timestamp": -1 },
  { name: "action_ts_desc", sparse: true }
);
print("  + idx: action_ts_desc (sparse)");

// 4) env 별 격리: portal 의 env switcher 가 환경별 audit 만 보여줌
db.audit_events.createIndex(
  { "metadata.env": 1, "timestamp": -1 },
  { name: "env_ts_desc", sparse: true }
);
print("  + idx: env_ts_desc (sparse)");

print("");
print("=== Phase N — audit_events 인덱스 확장 완료 ===");
print("ℹ TTL 정책은 cluster init script 의 expireAfterSeconds 로 env 별 분기 (N5 산출물):");
print("    prod    = 31_536_000  (1년)");
print("    staging =  7_776_000  (90일)");
print("    dev     =  2_592_000  (30일)");
print("ℹ 기존 audit_events 데이터는 변경 없음 — 신규 emit (audit_emitter.py, N1.3) 이 metadata.* 채움.");
