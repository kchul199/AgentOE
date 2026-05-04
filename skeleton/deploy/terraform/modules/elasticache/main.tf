# ──────────────────────────────────────────────────────────────────────────
# ElastiCache (Redis) 모듈
# - Replication group + Multi-AZ + automatic failover.
# - TLS in-transit + at-rest 암호화 강제.
# - AUTH 토큰은 Secrets Manager 에서 관리 (이 모듈은 ARN 만 참조).
# - Slow log + Engine log 를 CloudWatch 로 발행.
# - Staging 은 cache.t4g.small + 2 노드. Prod 는 cache.r7g.large+ 권장.
# ──────────────────────────────────────────────────────────────────────────

locals {
  name = "${var.project}-${var.environment}"
  tags = merge(var.tags, {
    Component = "elasticache"
    Engine    = "redis"
  })
}

# ── 서브넷 그룹 ────────────────────────────────────────────────────────────
resource "aws_elasticache_subnet_group" "this" {
  name        = local.name
  description = "Redis subnet group (private subnets across AZs)"
  subnet_ids  = var.subnet_ids
  tags        = local.tags
}

# ── 파라미터 그룹 ─────────────────────────────────────────────────────────
# maxmemory-policy: allkeys-lru → 캐시 용도. 세션 영속성이 필요한 키는 TTL 명시.
# notify-keyspace-events: 비활성. 필요 시 별도 RG로 분리 권장 (성능 영향).
resource "aws_elasticache_parameter_group" "this" {
  name        = "${local.name}-redis7"
  family      = "redis7"
  description = "Redis 7 parameter group for ${local.name}"

  parameter {
    name  = "maxmemory-policy"
    value = var.maxmemory_policy
  }

  parameter {
    name  = "tcp-keepalive"
    value = "60"
  }

  tags = local.tags
}

# ── 보안 그룹 ────────────────────────────────────────────────────────────
# 6379 inbound 는 EKS 워커 SG 에서만 허용. 외부 차단.
resource "aws_security_group" "this" {
  name        = "${local.name}-redis"
  description = "Allow Redis from EKS workers"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name}-redis" })
}

resource "aws_security_group_rule" "ingress_from_allowed_sgs" {
  for_each                 = toset(var.allowed_security_group_ids)
  type                     = "ingress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  source_security_group_id = each.value
  security_group_id        = aws_security_group.this.id
  description              = "Redis from ${each.value}"
}

# ── CloudWatch 로그 그룹 ─────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "slow" {
  count             = var.enable_logging ? 1 : 0
  name              = "/aws/elasticache/${local.name}/slow"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "engine" {
  count             = var.enable_logging ? 1 : 0
  name              = "/aws/elasticache/${local.name}/engine"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

# ── Replication Group ─────────────────────────────────────────────────────
resource "aws_elasticache_replication_group" "this" {
  replication_group_id        = local.name
  description                 = "Redis for ${local.name}"
  engine                      = "redis"
  engine_version              = var.engine_version
  node_type                   = var.node_type
  port                        = 6379
  parameter_group_name        = aws_elasticache_parameter_group.this.name
  subnet_group_name           = aws_elasticache_subnet_group.this.name
  security_group_ids          = [aws_security_group.this.id]

  num_cache_clusters          = var.num_cache_clusters
  automatic_failover_enabled  = var.num_cache_clusters > 1
  multi_az_enabled            = var.num_cache_clusters > 1

  at_rest_encryption_enabled  = true
  transit_encryption_enabled  = true
  auth_token                  = var.auth_token
  auth_token_update_strategy  = "ROTATE"

  snapshot_retention_limit    = var.snapshot_retention_days
  snapshot_window             = var.snapshot_window
  maintenance_window          = var.maintenance_window

  apply_immediately           = false
  auto_minor_version_upgrade  = true

  dynamic "log_delivery_configuration" {
    for_each = var.enable_logging ? [1] : []
    content {
      destination      = aws_cloudwatch_log_group.slow[0].name
      destination_type = "cloudwatch-logs"
      log_format       = "json"
      log_type         = "slow-log"
    }
  }

  dynamic "log_delivery_configuration" {
    for_each = var.enable_logging ? [1] : []
    content {
      destination      = aws_cloudwatch_log_group.engine[0].name
      destination_type = "cloudwatch-logs"
      log_format       = "json"
      log_type         = "engine-log"
    }
  }

  tags = merge(local.tags, { Name = local.name })

  lifecycle {
    # auth_token 회전은 Secrets Manager 자동 회전이 처리. tf 가 자꾸 되돌리면 안 됨.
    ignore_changes = [auth_token]
  }
}
