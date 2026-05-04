// MongoDB 초기 스키마 및 인덱스 생성
// Sprint 1: Agent, Call, Session 컬렉션 기본 구조

// 1. Agent 컬렉션 (콜봇 에이전트 설정)
db.createCollection("agents", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["_id", "name", "type", "status", "created_at"],
      properties: {
        _id: { bsonType: "objectId" },
        tenant_id: { bsonType: "string" },
        name: { bsonType: "string", description: "에이전트 이름" },
        type: { bsonType: "string", enum: ["voice_callbot", "asr", "tts"] },
        status: { bsonType: "string", enum: ["active", "inactive", "testing"] },
        config: {
          bsonType: "object",
          properties: {
            model: { bsonType: "string" },
            voice_id: { bsonType: "string" },
            language: { bsonType: "string" },
            system_prompt: { bsonType: "string" }
          }
        },
        metrics: {
          bsonType: "object",
          properties: {
            calls_total: { bsonType: "int" },
            calls_completed: { bsonType: "int" },
            avg_duration_ms: { bsonType: "int" },
            success_rate: { bsonType: "double" }
          }
        },
        created_at: { bsonType: "date" },
        updated_at: { bsonType: "date" }
      }
    }
  }
});

db.agents.createIndex({ "tenant_id": 1, "status": 1 });
db.agents.createIndex({ "type": 1 });

// 2. Call 컬렉션 (통화 기록)
db.createCollection("calls", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["_id", "agent_id", "status", "start_time"],
      properties: {
        _id: { bsonType: "objectId" },
        tenant_id: { bsonType: "string" },
        agent_id: { bsonType: "objectId" },
        session_id: { bsonType: "objectId" },
        phone_number: { bsonType: "string" },
        status: { bsonType: "string", enum: ["initiated", "connected", "ended", "failed"] },
        start_time: { bsonType: "date" },
        end_time: { bsonType: "date" },
        duration_ms: { bsonType: "int" },
        transcript: { bsonType: "string" },
        audio_url: { bsonType: "string" },
        metadata: {
          bsonType: "object",
          properties: {
            caller_id: { bsonType: "string" },
            fail_reason: { bsonType: "string" },
            ai_response_time_ms: { bsonType: "int" }
          }
        },
        created_at: { bsonType: "date" }
      }
    }
  }
});

db.calls.createIndex({ "tenant_id": 1, "agent_id": 1, "start_time": -1 });
db.calls.createIndex({ "session_id": 1 });
db.calls.createIndex({ "status": 1 });
db.calls.createIndex({ "start_time": -1 }, { expireAfterSeconds: 7776000 }); // 90일 TTL

// 3. Session 컬렉션 (멀티턴 세션)
db.createCollection("sessions", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["_id", "agent_id", "created_at"],
      properties: {
        _id: { bsonType: "objectId" },
        tenant_id: { bsonType: "string" },
        agent_id: { bsonType: "objectId" },
        user_id: { bsonType: "string" },
        status: { bsonType: "string", enum: ["active", "completed", "abandoned"] },
        turns: {
          bsonType: "array",
          items: {
            bsonType: "object",
            properties: {
              turn_id: { bsonType: "objectId" },
              user_input: { bsonType: "string" },
              ai_response: { bsonType: "string" },
              timestamp: { bsonType: "date" }
            }
          }
        },
        created_at: { bsonType: "date" },
        updated_at: { bsonType: "date" }
      }
    }
  }
});

db.sessions.createIndex({ "tenant_id": 1, "agent_id": 1, "created_at": -1 });
db.sessions.createIndex({ "user_id": 1 });
db.sessions.createIndex({ "status": 1 });

// 4. audit_events (감사 로그) — Time Series + WORM
//
// WORM(Write-Once-Read-Many) 보장은 다음 3단계로 달성:
//   (1) MongoDB 7+ Time Series 콜렉션 — bucket 내부 구조로 insert 만 효율적.
//   (2) auditWriter role (create_audit_role.js) — insert 권한만 부여.
//   (3) expireAfterSeconds 로 법정 보관 기한(기본 90일 × 12개월 = 약 3년) 이후 자동 삭제.
//
// 스키마 검증은 TS 콜렉션에 걸 수 없으므로 클라이언트측에서 형식 보장
// (app/repositories/audit_repository.py 의 AuditRepository.log 참조).
if (!db.getCollectionNames().includes("audit_events")) {
  db.createCollection("audit_events", {
    timeseries: {
      timeField: "timestamp",
      metaField: "metadata",
      granularity: "seconds"
    },
    // 3년 — 법정 보관 기한(통신사 통화 기록 1~3년)에 맞춤. 테넌트별 계약 차이는
    // application-layer 에서 archival 파이프라인으로 별도 보관.
    expireAfterSeconds: 94608000
  });

  // Time Series 콜렉션은 secondary 인덱스로 metadata.tenant_id + timestamp DESC 를 권장.
  // query path: {metadata.tenant_id: X, timestamp: {$gte: ..}} → 이 인덱스로 즉시 해결.
  db.audit_events.createIndex(
    { "metadata.tenant_id": 1, "timestamp": -1 },
    { name: "tenant_ts_desc" }
  );
  db.audit_events.createIndex(
    { "metadata.session_id": 1, "timestamp": -1 },
    { name: "session_ts_desc", sparse: true }
  );
  db.audit_events.createIndex(
    { "metadata.event_type": 1, "timestamp": -1 },
    { name: "event_type_ts_desc" }
  );
  print("✓ audit_events time-series collection created (WORM-enforced)");
} else {
  print("ℹ audit_events already exists — skipping");
}

print("✓ Schema initialization complete");
print("Collections created: agents, calls, sessions, audit_events");
print("ℹ auditWriter role is provisioned by mongo/create_audit_role.js (run after this).");
