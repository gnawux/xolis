#!/usr/bin/env python3

"""Validate the final PVM host or guest kernel configuration."""

from __future__ import annotations

import argparse
from pathlib import Path


HOST_EXACT = {
    "CONFIG_KVM": "m",
    "CONFIG_KVM_PVM": "m",
    "CONFIG_MODULES": "y",
    "CONFIG_MODULE_UNLOAD": "y",
    "CONFIG_PCI": "y",
    "CONFIG_PCI_MSI": "y",
    "CONFIG_NET_VENDOR_AMAZON": "y",
    "CONFIG_CGROUPS": "y",
    "CONFIG_CGROUP_BPF": "y",
    "CONFIG_BPF_SYSCALL": "y",
    "CONFIG_SECURITY_SELINUX": "y",
    "CONFIG_SERIAL_8250": "y",
    "CONFIG_SERIAL_8250_CONSOLE": "y",
    "CONFIG_DEBUG_FS": "y",
    "CONFIG_MAGIC_SYSRQ": "y",
}

HOST_BUILTIN_OR_MODULE = {
    "CONFIG_BLK_DEV_NVME",
    "CONFIG_ENA_ETHERNET",
    "CONFIG_XFS_FS",
    "CONFIG_EXT4_FS",
    "CONFIG_OVERLAY_FS",
    "CONFIG_NF_CONNTRACK",
    "CONFIG_VHOST_NET",
    "CONFIG_VHOST_VSOCK",
    "CONFIG_VIRTIO_VSOCKETS",
}

GUEST_EXACT = {
    "CONFIG_PVM_GUEST": "y",
    "CONFIG_X86_PIE": "y",
    "CONFIG_KVM_GUEST": "y",
    "CONFIG_PARAVIRT": "y",
    "CONFIG_PARAVIRT_XXL": "y",
    "CONFIG_PAGE_TABLE_ISOLATION": "y",
    "CONFIG_MODULES": "n",
    "CONFIG_KASAN": "n",
    "CONFIG_VSOCKETS": "y",
    "CONFIG_VIRTIO_VSOCKETS": "y",
    "CONFIG_VIRTIO_BLK": "y",
    "CONFIG_VIRTIO_NET": "y",
    "CONFIG_VIRTIO_CONSOLE": "y",
    "CONFIG_HW_RANDOM_VIRTIO": "y",
    "CONFIG_FUSE_FS": "y",
    "CONFIG_VIRTIO_FS": "y",
    "CONFIG_SERIAL_8250": "y",
    "CONFIG_SERIAL_8250_CONSOLE": "y",
    "CONFIG_DEBUG_FS": "y",
    "CONFIG_MAGIC_SYSRQ": "y",
}


def read_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("CONFIG_") and "=" in line:
            name, value = line.split("=", 1)
            values[name] = value.strip('"')
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            values[line.removeprefix("# ").removesuffix(" is not set")] = "n"
    return values


def validate_exact(values: dict[str, str], expected: dict[str, str]) -> list[str]:
    return [
        f"{name} must be {required}, found {values.get(name, 'missing')}"
        for name, required in sorted(expected.items())
        if values.get(name) != required
    ]


def validate_host(values: dict[str, str]) -> list[str]:
    failures = validate_exact(values, HOST_EXACT)
    failures.extend(
        f"{name} must be built in or a module, found {values.get(name, 'missing')}"
        for name in sorted(HOST_BUILTIN_OR_MODULE)
        if values.get(name) not in {"y", "m"}
    )
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("host", "guest"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    values = read_config(args.config)
    failures = (
        validate_host(values)
        if args.role == "host"
        else validate_exact(values, GUEST_EXACT)
    )
    if failures:
        for failure in failures:
            print(failure)
        return 1
    print(f"Validated {args.role} kernel config: {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
