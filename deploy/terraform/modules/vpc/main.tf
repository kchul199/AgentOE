# ──────────────────────────────────────────────────────────────────────────
# VPC 모듈
# - 3 AZ 분산 (a/b/c). EKS 컨트롤플레인 HA 보장.
# - public/private/intra 3-tier:
#     public  : NAT GW, ALB
#     private : EKS 워커, Elasticache
#     intra   : VPC endpoint, 외부로 라우팅 없음 (SSM, ECR, S3 등 PrivateLink 전용)
# - VPC Flow Logs 는 비용/디버깅 트레이드오프. Staging 은 REJECT 만 떨궈서 비용 낮춤.
# - NAT Gateway 는 Staging/Dev 에서 단일 GW 권장 (월 ~$32 절감). Prod 는 AZ당 1개.
# ──────────────────────────────────────────────────────────────────────────

locals {
  # AZ 개수에 맞춰 서브넷 CIDR 자동 분할.
  # /16 → /20 으로 쪼개면 4096개 IP, 각 tier x 3AZ = 9 서브넷 충분.
  public_subnets  = [for i, az in var.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  private_subnets = [for i, az in var.azs : cidrsubnet(var.vpc_cidr, 4, i + 4)]
  intra_subnets   = [for i, az in var.azs : cidrsubnet(var.vpc_cidr, 4, i + 8)]

  name = "${var.project}-${var.environment}"

  tags = merge(var.tags, {
    Project     = var.project
    Environment = var.environment
    Component   = "vpc"
    ManagedBy   = "terraform"
  })

  # EKS 가 ELB 자동 발견하기 위한 태그. 누락되면 LoadBalancer 서비스 ALB 가 안 올라옴.
  public_subnet_tags = {
    "kubernetes.io/role/elb"                        = "1"
    "kubernetes.io/cluster/${local.name}"           = "shared"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb"               = "1"
    "kubernetes.io/cluster/${local.name}"           = "shared"
    # Karpenter 가 빈 노드 프로비저닝할 때 이 태그로 서브넷 선택.
    "karpenter.sh/discovery"                        = local.name
  }
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(local.tags, { Name = local.name })
}

# ── 인터넷 게이트웨이 ───────────────────────────────────────────────────────
resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = "${local.name}-igw" })
}

# ── 서브넷 ────────────────────────────────────────────────────────────────
resource "aws_subnet" "public" {
  count                   = length(var.azs)
  vpc_id                  = aws_vpc.this.id
  cidr_block              = local.public_subnets[count.index]
  availability_zone       = var.azs[count.index]
  map_public_ip_on_launch = true

  tags = merge(local.tags, local.public_subnet_tags, {
    Name = "${local.name}-public-${var.azs[count.index]}"
    Tier = "public"
  })
}

resource "aws_subnet" "private" {
  count             = length(var.azs)
  vpc_id            = aws_vpc.this.id
  cidr_block        = local.private_subnets[count.index]
  availability_zone = var.azs[count.index]

  tags = merge(local.tags, local.private_subnet_tags, {
    Name = "${local.name}-private-${var.azs[count.index]}"
    Tier = "private"
  })
}

resource "aws_subnet" "intra" {
  count             = length(var.azs)
  vpc_id            = aws_vpc.this.id
  cidr_block        = local.intra_subnets[count.index]
  availability_zone = var.azs[count.index]

  tags = merge(local.tags, {
    Name = "${local.name}-intra-${var.azs[count.index]}"
    Tier = "intra"
  })
}

# ── NAT Gateway ───────────────────────────────────────────────────────────
# single_nat_gateway = true 면 1개만 생성 (비용 절감, AZ 장애 시 외부 트래픽 영향).
# Prod 에선 false 로 AZ 당 1개 권장.
resource "aws_eip" "nat" {
  count  = var.single_nat_gateway ? 1 : length(var.azs)
  domain = "vpc"
  tags   = merge(local.tags, { Name = "${local.name}-nat-eip-${count.index}" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_nat_gateway" "this" {
  count         = var.single_nat_gateway ? 1 : length(var.azs)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(local.tags, { Name = "${local.name}-nat-${count.index}" })

  depends_on = [aws_internet_gateway.this]
}

# ── 라우팅 테이블 ─────────────────────────────────────────────────────────
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = merge(local.tags, { Name = "${local.name}-rt-public" })
}

resource "aws_route_table_association" "public" {
  count          = length(var.azs)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = length(var.azs)
  vpc_id = aws_vpc.this.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = var.single_nat_gateway ? aws_nat_gateway.this[0].id : aws_nat_gateway.this[count.index].id
  }

  tags = merge(local.tags, { Name = "${local.name}-rt-private-${var.azs[count.index]}" })
}

resource "aws_route_table_association" "private" {
  count          = length(var.azs)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# intra 는 NAT 없음 — 인터넷으로 못 나감. PrivateLink 전용.
resource "aws_route_table" "intra" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = "${local.name}-rt-intra" })
}

resource "aws_route_table_association" "intra" {
  count          = length(var.azs)
  subnet_id      = aws_subnet.intra[count.index].id
  route_table_id = aws_route_table.intra.id
}

# ── VPC Flow Logs ─────────────────────────────────────────────────────────
# REJECT 만 떨궈도 보안 트리아지에 충분. ALL 은 비용 폭증.
resource "aws_cloudwatch_log_group" "flow" {
  count             = var.enable_flow_logs ? 1 : 0
  name              = "/aws/vpc/${local.name}/flow-logs"
  retention_in_days = var.flow_log_retention_days
  tags              = local.tags
}

resource "aws_iam_role" "flow" {
  count = var.enable_flow_logs ? 1 : 0
  name  = "${local.name}-vpc-flow-logs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "flow" {
  count = var.enable_flow_logs ? 1 : 0
  name  = "flow-logs"
  role  = aws_iam_role.flow[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_flow_log" "this" {
  count                = var.enable_flow_logs ? 1 : 0
  iam_role_arn         = aws_iam_role.flow[0].arn
  log_destination      = aws_cloudwatch_log_group.flow[0].arn
  traffic_type         = var.flow_log_traffic_type
  vpc_id               = aws_vpc.this.id
  max_aggregation_interval = 60

  tags = local.tags
}

# ── VPC Endpoints (PrivateLink) ───────────────────────────────────────────
# S3 / ECR / STS / Logs 는 트래픽이 많거나 NAT 비용 절감 효과가 큼.
# S3 는 Gateway Endpoint (무료), 나머지는 Interface Endpoint (시간당 과금 + 데이터).
data "aws_region" "current" {}

resource "aws_vpc_endpoint" "s3" {
  count             = var.enable_vpc_endpoints ? 1 : 0
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(aws_route_table.private[*].id, [aws_route_table.intra.id])

  tags = merge(local.tags, { Name = "${local.name}-vpce-s3" })
}

resource "aws_security_group" "vpce" {
  count       = var.enable_vpc_endpoints ? 1 : 0
  name        = "${local.name}-vpce"
  description = "Allow HTTPS from VPC to interface endpoints"
  vpc_id      = aws_vpc.this.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name}-vpce" })
}

resource "aws_vpc_endpoint" "interface" {
  for_each = var.enable_vpc_endpoints ? toset([
    "ecr.api",
    "ecr.dkr",
    "logs",
    "sts",
    "secretsmanager",
  ]) : []

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpce[0].id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${local.name}-vpce-${each.value}" })
}
