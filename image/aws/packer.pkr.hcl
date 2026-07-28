packer {
  required_plugins {
    amazon = {
      source  = "github.com/hashicorp/amazon"
      version = ">= 1.3.0"
    }
  }
}

variable "region" {
  type    = string
  default = "ap-northeast-1"
}

variable "source_ami_id" {
  type        = string
  description = "EKS-optimized AL2023 AMI ID for the target EKS Kubernetes version."
}

variable "subnet_id" {
  type        = string
  description = "Public subnet used only while building the image."
}

variable "kata_version" {
  type        = string
  description = "Pinned Kata Containers release version, without a leading v."
}

variable "kata_archive_url" {
  type        = string
  description = "HTTPS URL for the pinned Kata Containers static archive."
}

variable "kata_archive_sha256" {
  type        = string
  description = "SHA-256 checksum for the Kata Containers archive."
  sensitive   = true
}

variable "kata_source_commit" {
  type        = string
  description = "Immutable kata-containers source commit used to build the runtime-rs Dragonball shim."
}

variable "reuse_existing_kata_runtime" {
  type        = bool
  description = "Reuse and validate Kata from an immutable Xolis source AMI instead of rebuilding it."
  default     = false
}

variable "nydus_snapshotter_version" {
  type        = string
  description = "Optional pinned Nydus snapshotter release version, without a leading v. Leave empty for the Kata-only baseline."
  default     = ""
}

variable "nydus_snapshotter_archive_url" {
  type        = string
  description = "Optional HTTPS URL for the pinned Nydus snapshotter archive."
  default     = ""
}

variable "nydus_snapshotter_archive_sha256" {
  type        = string
  description = "Optional SHA-256 checksum for the Nydus snapshotter archive."
  sensitive   = true
  default     = ""
}

variable "nydus_daemon_version" {
  type        = string
  description = "Optional pinned Nydus image-service release version, without a leading v."
  default     = ""
}

variable "nydus_daemon_archive_url" {
  type        = string
  description = "Optional HTTPS URL for the pinned Nydus image-service archive."
  default     = ""
}

variable "nydus_daemon_archive_sha256" {
  type        = string
  description = "Optional SHA-256 checksum for the Nydus image-service archive."
  sensitive   = true
  default     = ""
}

source "amazon-ebs" "sandbox" {
  region                                = var.region
  source_ami                            = var.source_ami_id
  instance_type                         = "m8i.xlarge"
  ssh_username                          = "ec2-user"
  ssh_interface                         = "session_manager"
  pause_before_ssm                      = "30s"
  subnet_id                             = var.subnet_id
  associate_public_ip_address           = true
  temporary_security_group_source_cidrs = []
  ami_name                              = "xolis-sandbox-${formatdate("YYYYMMDDhhmm", timestamp())}"
  ami_description                       = "Xolis sandbox node: Kata Containers and Nydus, built from an EKS-optimized AL2023 AMI."

  launch_block_device_mappings {
    device_name = "/dev/xvda"
    # The fixed Kata 4.0.0 source build temporarily materializes the complete
    # Rust dependency graph as well as the 1.86 GiB static guest asset.
    volume_size           = 200
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }

  tags = {
    Project                 = "xolis"
    Environment             = "lab"
    ManagedBy               = "packer"
    KataVersion             = var.kata_version
    KataCommit              = var.kata_source_commit
    NydusSnapshotterVersion = var.nydus_snapshotter_version != "" ? var.nydus_snapshotter_version : "disabled"
    NydusDaemonVersion      = var.nydus_daemon_version != "" ? var.nydus_daemon_version : "disabled"
  }

  temporary_iam_instance_profile_policy_document {
    Version = "2012-10-17"

    Statement {
      Effect = "Allow"
      Action = [
        "ssm:UpdateInstanceInformation",
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
        "ec2messages:GetEndpoint",
        "ec2messages:GetMessages",
        "ec2messages:SendReply",
      ]
      Resource = ["*"]
    }
  }
}

build {
  sources = ["source.amazon-ebs.sandbox"]

  provisioner "file" {
    source      = "files/containerd-xolis-kata.toml"
    destination = "/tmp/containerd-xolis-kata.toml"
  }

  provisioner "file" {
    source      = "files/nydus-snapshotter.toml"
    destination = "/tmp/nydus-snapshotter.toml"
  }

  provisioner "file" {
    source      = "files/nydusd-config.fusedev.json"
    destination = "/tmp/nydusd-config.fusedev.json"
  }

  provisioner "file" {
    source      = "scripts/enable-containerd-import"
    destination = "/tmp/enable-containerd-import"
  }

  provisioner "shell" {
    environment_vars = [
      "KATA_VERSION=${var.kata_version}",
      "KATA_ARCHIVE_URL=${var.kata_archive_url}",
      "KATA_ARCHIVE_SHA256=${var.kata_archive_sha256}",
      "KATA_SOURCE_COMMIT=${var.kata_source_commit}",
      "REUSE_EXISTING_KATA_RUNTIME=${var.reuse_existing_kata_runtime}",
      "NYDUS_SNAPSHOTTER_VERSION=${var.nydus_snapshotter_version}",
      "NYDUS_SNAPSHOTTER_ARCHIVE_URL=${var.nydus_snapshotter_archive_url}",
      "NYDUS_SNAPSHOTTER_ARCHIVE_SHA256=${var.nydus_snapshotter_archive_sha256}",
      "NYDUS_DAEMON_VERSION=${var.nydus_daemon_version}",
      "NYDUS_DAEMON_ARCHIVE_URL=${var.nydus_daemon_archive_url}",
      "NYDUS_DAEMON_ARCHIVE_SHA256=${var.nydus_daemon_archive_sha256}",
    ]
    execute_command = "chmod +x {{ .Path }}; sudo /usr/bin/env {{ .Vars }} {{ .Path }}"
    script          = "scripts/install-runtime.sh"
  }
}
