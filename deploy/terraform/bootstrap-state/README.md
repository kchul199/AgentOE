# Terraform State Backend Bootstrap

이 모듈은 **다른 모든 terraform 모듈이 사용하기 전에 한 번만** 적용한다.

## 역할

- S3 버킷 — `.tfstate` 저장 (버전 관리, SSE-KMS, Public Access Block)
- DynamoDB 테이블 — state 락 (`LockID` 파티션 키)
- KMS 키 — state 암호화 전용

## 의존성

아무것도 없다. AWS 자격증명 + ap-northeast-2 접근 권한만 있으면 된다.
로컬 state (`terraform.tfstate`) 로 시작한 뒤, 모든 다른 환경은 여기서 만든 S3 를 backend 로 쓴다.

## 실행

```bash
cd deploy/terraform/bootstrap-state
terraform init
terraform apply \
  -var="project=agentoe" \
  -var="region=ap-northeast-2"
```

## 출력

- `state_bucket_name`   → 다른 env 의 `backend.tf` 에 써 넣는다.
- `state_lock_table`    → 동일.
- `state_kms_key_arn`   → 감사/IAM 정책에서 참조.

## 한 번만 실행

이 bootstrap 을 두 번 돌리면 state 버킷이 중복 생성되려 한다 (이름 충돌 → 실패).
첫 실행 후 git 에 backend 설정을 커밋하고, 이후엔 절대 다시 돌리지 않는다.
파괴는 "전체 서비스 폐기" 절차의 마지막에만 수행 (`terraform destroy` — 실수 방지 위해 `-auto-approve` 금지).
