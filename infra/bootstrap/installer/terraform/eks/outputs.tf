output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "efs_file_system_id" {
  description = "Put into infra/bootstrap/cluster/overlays/eks/efs-storageclass.yaml"
  value       = aws_efs_file_system.reports.id
}
