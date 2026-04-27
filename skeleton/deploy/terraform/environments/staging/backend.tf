# bootstrap-state 에서 출력한 값으로 치환해 사용.
# 최초에는 local backend 로 초기화 → 부트스트랩 완료 후 아래 블록을 활성화하고
# `terraform init -migrate-state` 로 옮긴다.
terraform {
  backend "s3" {
    bucket         = "agentoe-tf-state-REPLACE_ACCOUNT_ID-ap-northeast-2"
    key            = "environments/staging/terraform.tfstate"
    region         = "ap-northeast-2"
    dynamodb_table = "agentoe-tf-state-lock"
    encrypt        = true
    kms_key_id     = "alias/agentoe-tf-state"
  }
}
