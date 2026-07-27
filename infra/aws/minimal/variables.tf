variable "region" {
  description = "AWS Region that hosts the lab."
  type        = string
  default     = "ap-northeast-1"
}

variable "name" {
  description = "Short, unique name used as the prefix for lab resources."
  type        = string
  default     = "xolis-lab"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "name must start with a lowercase letter and contain only lowercase letters, digits, and hyphens."
  }
}

variable "kubernetes_version" {
  description = "Supported Amazon EKS Kubernetes version selected for the lab."
  type        = string
  default     = "1.35"
}

variable "vpc_cidr" {
  description = "IPv4 range for the lab VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "system_instance_type" {
  description = "Instance type for the EKS managed system node group."
  type        = string
  default     = "t3.large"
}

variable "sandbox_instance_type" {
  description = "Nested-virtualization-capable instance type for the self-managed sandbox ASG."
  type        = string
  default     = "m8i.xlarge"
}

variable "sandbox_ami_id" {
  description = "Optional custom EKS-compatible AL2023 sandbox AMI. An EKS-optimized AL2023 AMI is used while this is null."
  type        = string
  default     = null
  nullable    = true
}

variable "admin_principal_arn" {
  description = "IAM role ARN granted AmazonEKSClusterAdminPolicy access to the cluster. Set this to the IAM Identity Center role used for the lab."
  type        = string
}

variable "owner" {
  description = "Value used for the Owner tag."
  type        = string
  default     = "xolis"
}
