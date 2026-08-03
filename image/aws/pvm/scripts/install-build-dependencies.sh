#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "PVM kernel artifacts must be built on x86_64" >&2
  exit 1
fi

if [[ ! -f /etc/system-release ]] || ! grep -Fq "Amazon Linux release 2023" /etc/system-release; then
  echo "The pinned PVM builder must run on Amazon Linux 2023" >&2
  exit 1
fi

dnf install -y \
  bc \
  binutils \
  bison \
  dwarves \
  elfutils-libelf-devel \
  flex \
  gcc \
  git \
  hostname \
  make \
  openssl-devel \
  patch \
  perl \
  python3 \
  rpm-build \
  rsync \
  tar \
  xz
