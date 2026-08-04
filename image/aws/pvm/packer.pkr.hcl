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
  description = "Pinned EKS-optimized AL2023 x86_64 source AMI."
}

variable "subnet_id" {
  type        = string
  description = "Public build subnet used by the temporary SSM builder."
}

variable "artifact_bucket" {
  type        = string
  description = "Private S3 bucket containing the verified PVM kernel and runtime artifacts."
}

variable "builder_instance_type" {
  type        = string
  default     = "c7i.xlarge"
  description = "Temporary x86_64 builder type. Nested virtualization must remain disabled."
}

source "amazon-ebs" "pvm" {
  region                                = var.region
  source_ami                            = var.source_ami_id
  instance_type                         = var.builder_instance_type
  ssh_username                          = "ec2-user"
  ssh_interface                         = "session_manager"
  pause_before_ssm                      = "30s"
  subnet_id                             = var.subnet_id
  associate_public_ip_address           = true
  temporary_security_group_source_cidrs = []
  ami_name                              = "xolis-pvm-${formatdate("YYYYMMDDhhmm", timestamp())}"
  ami_description                       = "Xolis PVM node: pinned PVM host/guest kernels and Kata runtime-rs with upstream Dragonball."

  launch_block_device_mappings {
    device_name           = "/dev/xvda"
    volume_size           = 80
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }

  tags = {
    Project             = "xolis"
    Environment         = "lab"
    ManagedBy           = "packer"
    Virtualization      = "pvm"
    PVMCommit           = "91e9c9be4472756890844b2c982d7c72252dbfe6"
    PVMKernel           = "6.12.33-xolis-pvm"
    KataVersion         = "4.0.0"
    KataCommit          = "cf82bb35c80320178bf7570252fe75d6fb263209"
    HostRequiredArg     = "pti=off"
    NestedKVMWorkaround = "false"
    SourceAMI           = var.source_ami_id
    KernelManifestSHA   = "61f63839c192ee1eb61bd9abde40584ed352ac05f06e4392cf489ede6fdc15df"
    RuntimeManifestSHA  = "6b624dff525e1a126ba91ca30decc49bdae7f59ae3fd81d196efa4e819e33ca5"
    RuntimeArchiveSHA   = "ca4781bea4684834c6dda05f8b030795114b7be48868e210b6149944376b538b"
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

    Statement {
      Effect   = "Allow"
      Action   = ["s3:ListBucket"]
      Resource = ["arn:aws:s3:::${var.artifact_bucket}"]
    }

    Statement {
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:GetObjectVersion"]
      Resource = ["arn:aws:s3:::${var.artifact_bucket}/pvm/*", "arn:aws:s3:::${var.artifact_bucket}/kata/*"]
    }
  }
}

build {
  sources = ["source.amazon-ebs.pvm"]

  provisioner "shell" {
    inline = ["mkdir -p /tmp/xolis-pvm/files /tmp/xolis-pvm/scripts"]
  }

  provisioner "file" {
    source      = "versions.sh"
    destination = "/tmp/xolis-pvm/versions.sh"
  }

  provisioner "file" {
    source      = "files/xolis-pvm.modules-load.conf"
    destination = "/tmp/xolis-pvm/files/xolis-pvm.modules-load.conf"
  }

  provisioner "file" {
    source      = "scripts/install-ami-artifacts.sh"
    destination = "/tmp/xolis-pvm/scripts/install-ami-artifacts.sh"
  }

  provisioner "file" {
    source      = "scripts/validate-ami-host.sh"
    destination = "/tmp/xolis-pvm/scripts/validate-ami-host.sh"
  }

  provisioner "file" {
    source      = "scripts/validate-runtime.sh"
    destination = "/tmp/xolis-pvm/scripts/validate-runtime.sh"
  }

  provisioner "file" {
    source      = "../scripts/enable-containerd-import"
    destination = "/tmp/xolis-enable-containerd-import"
  }

  provisioner "shell" {
    inline = [
      "sudo /usr/bin/env PVM_ARTIFACT_BUCKET=${var.artifact_bucket} bash /tmp/xolis-pvm/scripts/install-ami-artifacts.sh",
    ]
  }

  provisioner "shell" {
    inline            = ["sudo systemctl reboot"]
    expect_disconnect = true
    pause_after       = "60s"
  }

  provisioner "shell" {
    inline = ["sudo /usr/local/lib/xolis-pvm/scripts/validate-ami-host.sh"]
  }
}
