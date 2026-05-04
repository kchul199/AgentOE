output "state_bucket_name" {
  description = "모든 env backend 에 쓰이는 S3 버킷 이름."
  value       = aws_s3_bucket.state.bucket
}

output "state_lock_table" {
  description = "State 락용 DynamoDB 테이블 이름."
  value       = aws_dynamodb_table.lock.name
}

output "state_kms_key_arn" {
  description = "State SSE-KMS 키 ARN."
  value       = aws_kms_key.state.arn
}
