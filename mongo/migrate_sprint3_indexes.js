// ─────────────────────────────────────────────────────────────────────────────
//  AgentOE — MongoDB 인덱스 마이그레이션 (Sprint 3 + Sprint 4)
//
//  적용 방법:
//    mongosh "mongodb://admin:<pass>@host:27017/?authSource=admin" \
//      --eval "use agentoe" migrate_sprint3_indexes.js
//
//  ⚠️  idempotent: 이미 존재하는 인덱스는 그냥 넘어갑니다
// ─────────────────────────────────────────────────────────────────────────────

// ── 1. sessions 컬렉션 ────────────────────────────────────────────────────────

// (Sprint 3) 테넌트별 활성 세션 수 집계용 — Admission Control에서 사용
db.sessions.createIndex(
  { tenant_id: 1, state: 1 },
  { name: "idx_sessions_tenant_state", background: true }
);

// (Sprint 3) session_id 단독 조회 (hot-state restore, WebSocket 연결 시)
db.sessions.createIndex(
  { session_id: 1 },
  { name: "idx_sessions_session_id", unique: true, background: true }
);

// (Sprint 3) 클라이언트별 통화 이력 페이징
db.sessions.createIndex(
  { tenant_id: 1, client_id: 1, created_at: -1 },
  { name: "idx_sessions_tenant_client_time", background: true }
);

// (Sprint 4) 이관(Transfer) 상태 분석 쿼리용
db.sessions.createIndex(
  { tenant_id: 1, transfer_reason: 1 },
  {
    name: "idx_sessions_tenant_transfer",
    sparse: true,              // transfer_reason 없는 문서는 인덱스 제외
    background: true
  }
);

// (운영) ENDED 상태 세션 자동 만료 — TTL 90일
db.sessions.createIndex(
  { ended_at: 1 },
  {
    name: "idx_sessions_ended_at_ttl",
    expireAfterSeconds: 7776000,  // 90일
    sparse: true,
    background: true
  }
);

print("✓ sessions indexes applied");

// ── 2. session_history 컬렉션 (Sprint 3 신규) ─────────────────────────────────

// 이미 컬렉션이 없으면 생성
if (!db.getCollectionNames().includes("session_history")) {
  db.createCollection("session_history");
  print("  ✓ session_history collection created");
}

// 핵심 조회 패턴: session_id + turn_index 범위 스캔 (reconnect 복원)
db.session_history.createIndex(
  { session_id: 1, turn_index: -1 },
  { name: "idx_history_session_turn", background: true }
);

// 테넌트 격리 — 테넌트별 전체 이력 집계/감사
db.session_history.createIndex(
  { tenant_id: 1, created_at: -1 },
  { name: "idx_history_tenant_time", background: true }
);

// 이력 자동 만료 — TTL 180일 (감사 목적 장기 보관)
db.session_history.createIndex(
  { created_at: 1 },
  {
    name: "idx_history_created_at_ttl",
    expireAfterSeconds: 15552000,  // 180일
    background: true
  }
);

print("✓ session_history indexes applied");

// ── 3. transfers 컬렉션 (Sprint 3 신규) ──────────────────────────────────────

if (!db.getCollectionNames().includes("transfers")) {
  db.createCollection("transfers");
  print("  ✓ transfers collection created");
}

// 세션별 이관 이력 조회
db.transfers.createIndex(
  { session_id: 1, requested_at: -1 },
  { name: "idx_transfers_session_time", background: true }
);

// 운영 대시보드: 테넌트별 이관 현황 (reason별 집계)
db.transfers.createIndex(
  { tenant_id: 1, reason: 1, status: 1 },
  { name: "idx_transfers_tenant_reason_status", background: true }
);

// 이관 데이터 자동 만료 — TTL 365일
db.transfers.createIndex(
  { requested_at: 1 },
  {
    name: "idx_transfers_requested_at_ttl",
    expireAfterSeconds: 31536000,  // 365일
    background: true
  }
);

print("✓ transfers indexes applied");

// ── 4. 인덱스 현황 출력 ───────────────────────────────────────────────────────
print("");
print("─── Index Summary ───────────────────────────────────────────────");
["sessions", "session_history", "transfers"].forEach(function(col) {
  var idxs = db[col].getIndexes();
  print("  " + col + " (" + idxs.length + " indexes):");
  idxs.forEach(function(i) {
    print("    - " + i.name);
  });
});
print("─────────────────────────────────────────────────────────────────");
print("Migration complete.");
