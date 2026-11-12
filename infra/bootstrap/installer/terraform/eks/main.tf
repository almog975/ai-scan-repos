terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_availability_zones" "available" {}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name               = "${var.cluster_name}-vpc"
  cidr               = "10.40.0.0/16"
  azs                = slice(data.aws_availability_zones.available.names, 0, 2)
  private_subnets    = ["10.40.1.0/24", "10.40.2.0/24"]
  public_subnets     = ["10.40.101.0/24", "10.40.102.0/24"]
  enable_nat_gateway = true
  single_nat_gateway = true
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.cluster_name
  cluster_version = var.kubernetes_version
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnets

  cluster_addons = {
    aws-ebs-csi-driver = {}
    aws-efs-csi-driver = {}
  }

  eks_managed_node_groups = {
    default = {
      instance_types = [var.node_instance_type]
      desired_size   = var.node_count
      min_size       = 1
      max_size       = 4
    }
  }
}

resource "aws_efs_file_system" "reports" {
  encrypted = true
  tags      = { Name = "${var.cluster_name}-reports" }
}

# TODO: aws_efs_mount_target per private subnet + SG allowing NFS from nodes; IRSA for EFS CSI.
