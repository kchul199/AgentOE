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

// 4. System Collections (향후 확장)
// audit_logs, webhooks, api_keys 등은 필요시 추가

print("✓ Schema initialization complete");
print("Collections created: agents, calls, sessions");
