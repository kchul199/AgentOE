// auditWriter role 프로비저닝 — audit_events 콜렉션에 대해 INSERT 만 허용.
//
// WORM 보장의 DB 계층 강제:
//   - insert           → 허용
//   - update / remove  → 거부 (role 에 action 자체가 없음 → Unauthorized)
//   - dropCollection   → 거부
//
// 실행:
//   mongosh --host mongo-primary:27017 \
//     -u admin -p $MONGO_ROOT_PASSWORD \
//     --authenticationDatabase admin \
//     /mongo-scripts/create_audit_role.js
//
// AgentOE 백엔드 컨테이너는 audit 경로에 대해 이 role 의 전용 사용자를 써야
// 애플리케이션 버그로도 감사 로그가 파괴되지 않음.
// 해당 사용자 생성 예:
//   db.createUser({
//     user: "agentoe_audit", pwd: "<secret>",
//     roles: [{ role: "auditWriter", db: "agentoe" }]
//   });
// app/repositories/audit_repository.py 에서 별도 Motor 클라이언트로 이 사용자로 접속.

use agentoe;

// 이미 존재하면 idempotent 하게 재생성 (actions 변경 반영).
try {
  db.dropRole("auditWriter");
  print("ℹ existing auditWriter role dropped — will recreate");
} catch (e) {
  // 처음 실행이면 RolesNotFound — 무시.
}

db.createRole({
  role: "auditWriter",
  privileges: [
    {
      resource: { db: "agentoe", collection: "audit_events" },
      // 주의: find 는 쿼리 API 용도로 유지. 제거하면 /api/v1/audit 조회가 깨짐.
      // update/remove/drop 이 의도적으로 **빠져있음** — 이게 WORM 강제의 핵심.
      actions: ["insert", "find"]
    },
    {
      resource: { db: "agentoe", collection: "system.buckets.audit_events" },
      // Time Series 는 내부적으로 system.buckets.* 에 저장 — insert 도 거기로 전파됨.
      actions: ["insert", "find"]
    }
  ],
  roles: []
});

print("✓ auditWriter role created on agentoe DB");
print("  privileges: insert, find  (NO update / remove / drop)");
print("  next step: create a dedicated user with this role for AuditRepository.");
