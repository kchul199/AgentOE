// migrate_phase_n_portal_users.js — Phase N (운영포탈) 운영자 계정 컬렉션
//
// 적용 시점: Phase N1.1 — 운영포탈 plan v2.1 의 G2 closing 산출물.
//
// 핵심:
//   - portal_users 는 운영포탈 전용 운영자 계정 저장소 (멀티 tenant 와 무관).
//   - 기존 backend 의 JWT 발급 (/api/v1/auth/token) 과는 별개 — issuer="agentoe-portal".
//   - bcrypt (cost=12) 비밀번호 해시 + TOTP MFA + brute-force 단계적 잠금 + refresh denylist.
//   - portal_roles 은 ["portal:viewer" | "portal:operator" | "portal:admin"] 중 1개 이상.
//
// Idempotent — 여러 번 실행해도 안전.
//
// 사용:
//   mongosh "$MONGODB_URI" --quiet mongo/migrate_phase_n_portal_users.js

print("=== Phase N — portal_users 컬렉션 생성 시작 ===");

if (!db.getCollectionNames().includes("portal_users")) {
  db.createCollection("portal_users", {
    validator: {
      $jsonSchema: {
        bsonType: "object",
        required: ["username", "password_hash", "portal_roles", "active", "created_at"],
        properties: {
          username:        { bsonType: "string", description: "로그인 식별자 (email 또는 username)" },
          password_hash:   { bsonType: "string", description: "bcrypt cost=12" },
          mfa_secret_enc:  { bsonType: ["string", "null"], description: "AES-GCM 암호화된 TOTP base32 secret (envelope key)" },
          mfa_enrolled:    { bsonType: "bool", description: "MFA 등록 여부 — false 면 첫 로그인 후 강제 enroll" },
          mfa_backup_codes_hash: {
            bsonType: ["array", "null"],
            description: "8개 백업 코드의 bcrypt 해시 (1회 사용 — 사용 시 null 마킹)",
            items: { bsonType: ["string", "null"] }
          },
          portal_roles: {
            bsonType: "array",
            minItems: 1,
            description: "portal:viewer / portal:operator / portal:admin 중 1개 이상",
            items: {
              bsonType: "string",
              enum: ["portal:viewer", "portal:operator", "portal:admin"]
            }
          },
          legacy_roles: {
            bsonType: ["array", "null"],
            description: "기존 admin/super_admin 호환 — 거의 사용 안 함",
            items: { bsonType: "string" }
          },
          tenant_scope: {
            bsonType: ["array", "null"],
            description: "null = 모든 tenant 접근. 배열 = 지정 tenant 만",
            items: { bsonType: "string" }
          },
          active:          { bsonType: "bool", description: "비활성화 = soft delete" },
          created_at:      { bsonType: "date" },
          updated_at:      { bsonType: ["date", "null"] },
          last_login_at:   { bsonType: ["date", "null"] },
          failed_attempts: { bsonType: "int", minimum: 0, description: "연속 실패 카운터 — 성공 시 0 reset" },
          locked_until:    { bsonType: ["date", "null"], description: "이 시각까지 로그인 차단" },
          created_by:      { bsonType: ["string", "null"], description: "이 계정을 만든 portal:admin client_id" }
        }
      }
    },
    validationLevel: "moderate",   // 기존 도큐먼트가 있는 경우 strict 가 아닌 moderate
    validationAction: "error"
  });

  // unique 인덱스 — username 중복 차단
  db.portal_users.createIndex({ username: 1 }, { name: "username_unique", unique: true });
  // active 필터 + 정렬
  db.portal_users.createIndex({ active: 1, last_login_at: -1 }, { name: "active_last_login" });

  print("✓ portal_users 컬렉션 생성 (with schema validator + 2 indexes)");
} else {
  // 이미 존재하면 인덱스만 idempotent 추가 (collMod 로 validator 갱신은 별도 PR)
  db.portal_users.createIndex({ username: 1 }, { name: "username_unique", unique: true });
  db.portal_users.createIndex({ active: 1, last_login_at: -1 }, { name: "active_last_login" });
  print("ℹ portal_users 이미 존재 — 인덱스만 idempotent 보장");
}

print("");
print("=== Phase N — portal_users 컬렉션 생성 완료 ===");
print("ℹ 초기 portal:admin 계정 생성은 별도 스크립트 (mongo/seed_portal_admin.js, N1.7 산출):");
print("    PORTAL_BOOTSTRAP_ADMIN_USERNAME=ops@agentoe.io \\");
print("    PORTAL_BOOTSTRAP_ADMIN_PASSWORD='<강력 패스워드>' \\");
print("    mongosh \"$MONGODB_URI\" --quiet mongo/seed_portal_admin.js");
print("ℹ 패스워드 정책 / MFA enroll 흐름은 N1.7 의 auth_portal.py 책임.");
