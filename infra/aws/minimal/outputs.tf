output "cluster_name" {
  description = "Name of the EKS cluster."
  value       = aws_eks_cluster.this.name
}

output "region" {
  description = "AWS Region of the lab."
  value       = var.region
}

output "sandbox_autoscaling_group" {
  description = "Name of the self-managed sandbox Auto Scaling group."
  value       = aws_autoscaling_group.sandbox.name
}

output "sandbox_node_selector" {
  description = "Selector used by the lab tool to identify sandbox nodes."
  value       = "xolis.io/kata-ready=true"
}

output "configure_kubectl" {
  description = "Command that writes kubeconfig for the lab cluster."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${aws_eks_cluster.this.name}"
}
