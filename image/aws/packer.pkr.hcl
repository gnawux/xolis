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

variable "nydus_version" {
  type        = string
  description = "Pinned Nydus release version, without a leading v."
}

variable "nydus_archive_url" {
  type        = string
  description = "HTTPS URL for the pinned Nydus static archive."
}

variable "nydus_archive_sha256" {
  type        = string
  description = "SHA-256 checksum for the Nydus archive."
  sensitive   = true
}

source "amazon-ebs" "sandbox" {
  region                      = var.region
  source_ami                  = var.source_ami_id
  instance_type               = "m8i.xlarge"
  ssh_username                = "ec2-user"
  subnet_id                   = var.subnet_id
  associate_public_ip_address = true
  ami_name                    = "xolis-sandbox-${formatdate("YYYYMMDDhhmm", timestamp())}"
  ami_description             = "Xolis sandbox node: Kata Containers and Nydus, built from an EKS-optimized AL2023 AMI."

  launch_block_device_mappings {
    device_name           = "/dev/xvda"
    volume_size           = 40
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }

  tags = {
    Project      = "xolis"
    Environment  = "lab"
    ManagedBy    = "packer"
    KataVersion  = var.kata_version
    NydusVersion = var.nydus_version
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
    source      = "scripts/enable-containerd-import"
    destination = "/tmp/enable-containerd-import"
  }

  provisioner "shell" {
    environment_vars = [
      "KATA_VERSION=${var.kata_version}",
      "KATA_ARCHIVE_URL=${var.kata_archive_url}",
      "KATA_ARCHIVE_SHA256=${var.kata_archive_sha256}",
      "NYDUS_VERSION=${var.nydus_version}",
      "NYDUS_ARCHIVE_URL=${var.nydus_archive_url}",
      "NYDUS_ARCHIVE_SHA256=${var.nydus_archive_sha256}",
    ]
    execute_command = "chmod +x {{ .Path }}; sudo -E {{ .Path }}"
    script          = "scripts/install-runtime.sh"
  }
}
