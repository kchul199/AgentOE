# Staging Terraform

이 디렉토리는 `agentoe-staging` 환경의 모든 클라우드 리소스를 정의합니다.

## 구성

| 파일 | 역할 |
| --- | --- |
| `backend.tf` | S3 + DynamoDB lock 원격 state. `bootstrap-state` 출력으로 치환. |
| `variables.tf` | 입력 정의. `terraform.tfvars` 또는 `-var-file` 로 주입. |
| `main.tf` | VPC / EKS / ECR / Secrets / Redis / Atlas / IRSA 컴포지션. |
| `outputs.tf` | kubeconfig 명령, IRSA ARN, Secret ARN 등 다른 stack 이 사용. |
| `terraform.tfvars.example` | 입력 변수 샘플. 실제 값은 별도 파일 (gitignore). |

## 부트스트랩 절차

1. **State backend**
   ```sh
   cd ../../bootstrap-state
   terraform init && terraform apply
   ```
   출력에서 버킷명 / DDB 테이블명 / KMS alias 를 받아 `backend.tf` 의
   `REPLACE_ACCOUNT_ID` 자리를 치환.

2. **Atlas 자격 증명**
   ```sh
   export MONGODB_ATLAS_PUBLIC_KEY=...
   export MONGODB_ATLAS_PRIVATE_KEY=...
   ```

3. **변수 파일 작성**
   ```sh
   cp terraform.tfvars.example terraform.tfvars
   # admin_cidrs / atlas_org_id / domain_name 등 채움
   ```

4. **초기화 + 적용**
   ```sh
   terraform init
   terraform plan -out tfplan
   terraform apply tfplan
   ```

   첫 적용은 의존성이 깊어 느립니다. 단계별 적용을 권장:
   ```sh
   terraform apply -target=module.vpc
   terraform apply -target=module.eks
   terraform apply
   ```

5. **kubeconfig 설정**
   ```sh
   $(terraform output -raw kubeconfig_command)
   kubectl get nodes
   ```

## 주의

- `admin_cidrs` 는 반드시 VPN/Bastion 출구 IP 만. `0.0.0.0/0` 절대 금지.
- 자동 생성된 Redis AUTH / Mongo password 는 Secrets Manager 에 저장됩니다.
  외부에서 직접 가져오지 말고 ESO 로 K8s Secret 으로 동기화해 사용.
- NAT public IP 가 변경되면 Atlas allowlist 가 자동으로 갱신되지만
  변경 시점에 일시적으로 연결이 끊길 수 있으니 점검 윈도우에 적용.
