// AgentOE MongoDB Schema Initialization Script
// Run: mongosh "mongodb://admin:pass@localhost:27017/?authSource=admin" init_schema.js

const DB_NAME = "agentoe";
const db = db.getSiblingDB(DB_NAME);

print("=== AgentOE MongoDB Schema Initialization ===");

// ─── 1. sessions ───────────────────────────────────────────────────────────
db.createCollection("sessions");
db.sessions.createIndex({ "session_id": 1 }, { unique: true, name: "idx_session_id_unique" });
db.sessions.createIndex({ "tenant_id": 1, "status": 1 }, { name: "idx_tenant_status" });
db.sessions.createIndex({ "tenant_id": 1, "created_at": -1 }, { name: "idx_tenant_created" });
db.sessions.createIndex(
  { "expires_at": 1 },
  { expireAfterSeconds: 0, name: "idx_ttl_expires" }
);
print("✓ sessions collection ready");

// ─── 2. audit_events (Time Series) ─────────────────────────────────────────
db.createCollection("audit_events", {
  timeseries: {
    timeField: "timestamp",
    metaField: "metadata",
    granularity: "seconds"
  },
  expireAfterSeconds: 7776000 // 90 days TTL
});
db.audit_events.createIndex(
  { "metadata.tenant_id": 1, "timestamp": -1 },
  { name: "idx_tenant_timestamp" }
);
db.audit_events.createIndex(
  { "metadata.session_id": 1, "timestamp": -1 },
  { name: "idx_session_timestamp" }
);
print("✓ audit_events (Time Series) collection ready");

// ─── 3. policies ────────────────────────────────────────────────────────────
db.createCollection("policies");
db.policies.createIndex({ "policy_id": 1 }, { unique: true, name: "idx_policy_id_unique" });
db.policies.createIndex({ "tenant_id": 1, "level": 1 }, { name: "idx_tenant_level" });
db.policies.createIndex({ "tenant_id": 1, "scenario_ids": 1 }, { name: "idx_tenant_scenario" });
print("✓ policies collection ready");

// ─── 4. connectors ──────────────────────────────────────────────────────────
db.createCollection("connectors");
db.connectors.createIndex({ "connector_id": 1 }, { unique: true, name: "idx_connector_id_unique" });
db.connectors.createIndex({ "tenant_id": 1, "type": 1 }, { name: "idx_tenant_type" });
db.connectors.createIndex({ "tenant_id": 1, "enabled": 1 }, { name: "idx_tenant_enabled" });
print("✓ connectors collection ready");

// ─── 5. tenants ─────────────────────────────────────────────────────────────
db.createCollection("tenants");
db.tenants.createIndex({ "tenant_id": 1 }, { unique: true, name: "idx_tenant_id_unique" });
db.tenants.createIndex({ "plan": 1, "enabled": 1 }, { name: "idx_plan_enabled" });
print("✓ tenants collection ready");

// ─── 6. kill_switches ───────────────────────────────────────────────────────
db.createCollection("kill_switches");
db.kill_switches.createIndex({ "switch_id": 1 }, { unique: true, name: "idx_switch_id_unique" });
db.kill_switches.createIndex({ "scope": 1, "target_id": 1, "active": 1 }, { name: "idx_scope_target" });
print("✓ kill_switches collection ready");

// ─── 7. circuit_breaker_events ──────────────────────────────────────────────
db.createCollection("circuit_breaker_events", {
  timeseries: {
    timeField: "timestamp",
    metaField: "service_meta",
    granularity: "seconds"
  },
  expireAfterSeconds: 604800 // 7 days
});
print("✓ circuit_breaker_events (Time Series) collection ready");

// ─── 8. users (Admin Console) ───────────────────────────────────────────────
db.createCollection("users");
db.users.createIndex({ "user_id": 1 }, { unique: true, name: "idx_user_id_unique" });
db.users.createIndex({ "tenant_id": 1, "email": 1 }, { unique: true, name: "idx_tenant_email_unique" });
db.users.createIndex({ "tenant_id": 1, "role": 1 }, { name: "idx_tenant_role" });
print("✓ users collection ready");

// ─── 9. scenarios ───────────────────────────────────────────────────────────
db.createCollection("scenarios");
db.scenarios.createIndex({ "scenario_id": 1, "tenant_id": 1 }, { unique: true, name: "idx_scenario_tenant_unique" });
db.scenarios.createIndex({ "tenant_id": 1, "enabled": 1 }, { name: "idx_tenant_enabled" });
print("✓ scenarios collection ready");

// ─── Seed: default admin user ───────────────────────────────────────────────
db.users.insertOne({
  user_id: "usr_admin_default",
  tenant_id: "system",
  email: "admin@agentoe.io",
  role: "super_admin",
  created_at: new Date(),
  enabled: true
});

print("\n=== Schema initialization complete ===");
print(`Database: ${DB_NAME}`);
print(`Collections: ${db.getCollectionNames().join(", ")}`);
