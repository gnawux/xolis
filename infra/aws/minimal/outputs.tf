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

output "pvm_autoscaling_group" {
  description = "Name of the optional PVM sandbox Auto Scaling group, or null when disabled."
  value       = try(aws_autoscaling_group.pvm[0].name, null)
}

output "pvm_node_selector" {
  description = "Selector used to identify PVM-capable sandbox nodes."
  value       = "xolis.io/pvm-ready=true"
}

output "configure_kubectl" {
  description = "Command that writes kubeconfig for the lab cluster."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${aws_eks_cluster.this.name}"
}

output "image_repository_urls" {
  description = "Private ECR repositories used by Xolis images."
  value       = { for name, repository in aws_ecr_repository.images : name => repository.repository_url }
}

output "image_builder_instance_profile" {
  description = "IAM instance profile for temporary SSM-managed image builders."
  value       = aws_iam_instance_profile.image_builder.name
}

output "image_builder_security_group_id" {
  description = "No-ingress security group for temporary image builders."
  value       = aws_security_group.image_builder.id
}
