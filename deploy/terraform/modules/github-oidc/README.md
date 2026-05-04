# module: github-oidc

GitHub Actions ↔ AWS 의 keyless 인증 설정. OIDC provider + 워크로드별 IAM role 2~3개.

## 만들어주는 것

| 리소스                     | 책임                                               |
|---------------------------|----------------------------------------------------|
| `aws_iam_openid_connect_provider.github` | `https://token.actions.githubusercontent.com` 신뢰 |
| `aws_iam_role.ecr_push`   | ECR push 만 — main / tag push 워크플로            |
| `aws_iam_role.eks_deploy` | EKS describe + (in-cluster RBAC 위해 aws-auth) — staging/production GitHub Environment 한정 |
| `aws_iam_role.tf_plan`    | (옵션) PR 에서 `terraform plan` 만 — RO            |

## 사용 예 (environments/staging/main.tf 안에서)

```hcl
module "github_oidc" {
  source = "../../modules/github-oidc"

  name_prefix          = "${var.project_name}-${var.env}"
  aws_region           = var.aws_region
  github_org           = "agentoe"
  github_repo          = "agentoe"
  create_oidc_provider = true                  # 계정에 처음 만들 때만 true

  ecr_repository_names = [
    "${var.project_name}-${var.env}/backend",
    "${var.project_name}-${var.env}/vbgw",
    "${var.project_name}-${var.env}/frontend",
  ]
  eks_cluster_names = [module.eks.cluster_name]
}
```

## 다음 단계 (Terraform 외부에서 수동)

1. **GitHub repo Settings → Secrets and variables → Actions → Variables** 에 추가:
   - `AWS_ECR_PUSH_ROLE_ARN`  — `module.github_oidc.ecr_push_role_arn`
   - `AWS_EKS_DEPLOY_ROLE_ARN` — `module.github_oidc.eks_deploy_role_arn`
   - `AWS_REGION`, `ECR_REGISTRY` (12자리계정.dkr.ecr.region.amazonaws.com)
   - `EKS_CLUSTER_NAME`

2. **EKS aws-auth ConfigMap** 에 `eks_deploy_role_arn` 매핑 추가:

   ```bash
   eksctl create iamidentitymapping \
     --cluster "$CLUSTER_NAME" \
     --region "$AWS_REGION" \
     --arn "$EKS_DEPLOY_ROLE_ARN" \
     --group system:masters \
     --username github-actions-deploy
   ```

   prod 에서는 `system:masters` 대신 `kubectl` namespace-scoped Role 매핑 권장.

3. **GitHub Environments** (`staging`, `production`) 생성 — production 에 required reviewers 설정.
   trust policy 의 `:environment:` 조건이 이 게이트 통과 시에만 토큰을 발급받게 한다.
