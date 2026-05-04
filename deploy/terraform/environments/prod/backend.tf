# bootstrap-state 에서 출력한 값으로 치환.
# prod 의 state 는 staging 과 별도 key — 권한 분리 + blast radius 격리.
terraform {
  backend "s3" {
    bucket         = "agentoe-tf-state-REPLACE_ACCOUNT_ID-ap-northeast-2"
    key            = "environments/prod/terraform.tfstate"
    region         = "ap-northeast-2"
    dynamodb_table = "agentoe-tf-state-lock"
    encrypt        = true
    kms_key_id     = "alias/agentoe-tf-state"
  }
}
