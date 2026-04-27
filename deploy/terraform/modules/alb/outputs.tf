output "certificate_arn" {
  description = "발급된 ACM 인증서 ARN. K8s Ingress annotation 에 박음."
  value       = try(aws_acm_certificate.this[0].arn, null)
}

output "validation_completed" {
  description = "ACM DNS validation 완료 여부 (true/false)."
  value       = length(aws_acm_certificate_validation.this) > 0
}

output "primary_record_fqdn" {
  description = "primary hostname FQDN (alias 생성된 경우)."
  value       = try(aws_route53_record.primary[0].fqdn, null)
}

output "hosted_zone_id" {
  value = try(data.aws_route53_zone.this[0].zone_id, null)
}
