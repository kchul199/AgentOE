# ──────────────────────────────────────────────────────────────────────────
# ALB 모듈 (Ingress 용 데이터 패스 보조)
# - 일반적으로는 K8s 의 AWS Load Balancer Controller 가 ALB 를 직접 만든다.
# - 이 모듈은 (a) ACM 인증서 발급/검증, (b) Route53 alias 레코드 + 옵션의
#   "외부 ALB 1개" 같은 명시적 케이스를 위해 자리만 잡아둔다.
# - 인증서는 Route53 DNS validation 으로 자동 발급.
# ──────────────────────────────────────────────────────────────────────────

locals {
  tags = merge(var.tags, { Component = "alb" })
}

# ── Route53 hosted zone ───────────────────────────────────────────────────
data "aws_route53_zone" "this" {
  count        = var.domain_name != "" ? 1 : 0
  name         = var.domain_name
  private_zone = false
}

# ── ACM 인증서 ────────────────────────────────────────────────────────────
resource "aws_acm_certificate" "this" {
  count                     = var.domain_name != "" ? 1 : 0
  domain_name               = var.primary_hostname
  subject_alternative_names = var.alt_hostnames
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = merge(local.tags, { Name = var.primary_hostname })
}

resource "aws_route53_record" "validation" {
  for_each = var.domain_name != "" ? {
    for d in aws_acm_certificate.this[0].domain_validation_options : d.domain_name => {
      name   = d.resource_record_name
      record = d.resource_record_value
      type   = d.resource_record_type
    }
  } : {}

  zone_id = data.aws_route53_zone.this[0].zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 60

  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "this" {
  count                   = var.domain_name != "" ? 1 : 0
  certificate_arn         = aws_acm_certificate.this[0].arn
  validation_record_fqdns = [for r in aws_route53_record.validation : r.fqdn]
}

# ── Route53 alias 레코드 (선택) ───────────────────────────────────────────
# K8s Ingress 가 ALB 를 만든 다음, 그 ALB DNS 에 alias 를 거는 별도 단계가 더 안전.
# 모듈에서는 외부에서 alb_dns_name / alb_zone_id 를 받아 alias 만 생성.
resource "aws_route53_record" "primary" {
  count   = var.create_alias && var.alb_dns_name != "" ? 1 : 0
  zone_id = data.aws_route53_zone.this[0].zone_id
  name    = var.primary_hostname
  type    = "A"

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_zone_id
    evaluate_target_health = true
  }
}
