# ──────────────────────────────────────────────────────────────────────────
# MongoDB Atlas 모듈
# - Project + Cluster + DB user + IP allowlist + VPC peering(또는 PrivateLink).
# - Staging: M10 (~$60/월), Prod: M30+ 권장.
# - Atlas API key 는 환경변수 MONGODB_ATLAS_PUBLIC_KEY/PRIVATE_KEY 로 provider 에 주입.
# - 비밀번호는 Secrets Manager → ESO 로 K8s 에 마운트.
# ──────────────────────────────────────────────────────────────────────────

locals {
  cluster_name = "${var.project}-${var.environment}"

  tags = merge(var.tags, {
    Component = "atlas"
    Engine    = "mongodb"
  })
}

resource "mongodbatlas_project" "this" {
  name   = local.cluster_name
  org_id = var.org_id

  # 클라우드 비용/리스크 알림은 Atlas UI 에서 별도 셋업 권장.
  is_collect_database_specifics_statistics_enabled = true
  is_data_explorer_enabled                          = true
  is_performance_advisor_enabled                    = true
  is_realtime_performance_panel_enabled             = true
  is_schema_advisor_enabled                         = true
}

# ── 클러스터 ──────────────────────────────────────────────────────────────
# AWS 의 ap-northeast-2 와 동일 리전. cross-region 은 비싸지만 필요 시 추가 region_configs.
resource "mongodbatlas_advanced_cluster" "this" {
  project_id     = mongodbatlas_project.this.id
  name           = local.cluster_name
  cluster_type   = "REPLICASET"
  backup_enabled = true

  replication_specs {
    region_configs {
      provider_name = "AWS"
      region_name   = var.atlas_region
      priority      = 7

      electable_specs {
        instance_size = var.instance_size
        node_count    = 3
      }
    }
  }

  # MongoDB 7.0 권장 (Time Series + JSON Schema 검증 안정).
  mongo_db_major_version = var.mongo_version

  # 자동 백업: 매일 + 시간단위. PITR 도 함께 활성화.
  pit_enabled = true

  advanced_configuration {
    minimum_enabled_tls_protocol = "TLS1_2"
    javascript_enabled           = false
    # 운영 안정성: 안 쓰는 기능은 다 끄는 편이 안전.
  }

  tags {
    key   = "Project"
    value = var.project
  }
  tags {
    key   = "Environment"
    value = var.environment
  }
  tags {
    key   = "ManagedBy"
    value = "terraform"
  }
}

# ── DB 사용자 ──────────────────────────────────────────────────────────────
# 비번은 random_password 로 생성, Secrets Manager 에 모듈 외부에서 저장.
resource "mongodbatlas_database_user" "app" {
  project_id         = mongodbatlas_project.this.id
  username           = var.db_username
  password           = var.db_password
  auth_database_name = "admin"

  roles {
    role_name     = "readWrite"
    database_name = var.app_database_name
  }

  # 추가 DB (예: scenarios, audit_logs) 가 있으면 roles 블록 더 추가.
  dynamic "roles" {
    for_each = var.additional_database_roles
    content {
      role_name     = roles.value.role_name
      database_name = roles.value.database_name
    }
  }

  scopes {
    name = mongodbatlas_advanced_cluster.this.name
    type = "CLUSTER"
  }
}

# ── IP Allowlist ──────────────────────────────────────────────────────────
# NAT EIP 와 admin CIDR 만 허용.
resource "mongodbatlas_project_ip_access_list" "nat" {
  for_each   = toset(var.allowed_cidrs)
  project_id = mongodbatlas_project.this.id
  cidr_block = each.value
  comment    = "NAT/Admin allowlist (terraform)"
}

# ── VPC 연동 (옵션) ───────────────────────────────────────────────────────
# 권장: PrivateLink (Atlas → AWS PrivateLink). VPC peering 보다 운영이 단순.
# 모듈은 PrivateLink 자리만 잡아둠. enable_privatelink=true 로 활성.
resource "mongodbatlas_privatelink_endpoint" "this" {
  count          = var.enable_privatelink ? 1 : 0
  project_id     = mongodbatlas_project.this.id
  provider_name  = "AWS"
  region         = var.atlas_region
}

# AWS 쪽 endpoint 는 VPC 측에서 별도 생성 후 service_endpoint_id 로 연결.
# (모듈 외부 wiring 요구 — env 단계에서 aws_vpc_endpoint 와 연결.)
