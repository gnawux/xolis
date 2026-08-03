import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
PVM = ROOT / "image/aws/pvm"


class PvmAssetTests(unittest.TestCase):
    def test_versions_pin_the_build_inputs(self) -> None:
        versions = (PVM / "versions.sh").read_text(encoding="utf-8")
        self.assertIn(
            'PVM_SOURCE_COMMIT="91e9c9be4472756890844b2c982d7c72252dbfe6"',
            versions,
        )
        self.assertIn('PVM_KERNEL_VERSION="6.12.33"', versions)
        self.assertIn('EKS_AMI_RELEASE_VERSION="1.35.6-20260728"', versions)
        self.assertIn('EKS_SOURCE_AMI_ID="ami-0f15c654d8131cadd"', versions)
        self.assertIn('EKS_REFERENCE_KERNEL_VERSION="6.12.94-123.192.amzn2023"', versions)
        self.assertIn(
            'EKS_HOST_CONFIG_SHA256="701a459dda4c3bfecb322eea5c465d071b0f21e1101839a5e3637f43e207dad6"',
            versions,
        )
        self.assertIn('KATA_VERSION="4.0.0"', versions)
        self.assertIn('PVM_HOST_REQUIRED_CMDLINE="pti=off"', versions)
        self.assertIn("export PVM_SOURCE_URL PVM_SOURCE_COMMIT", versions)

    def test_build_requires_a_pinned_host_config_digest(self) -> None:
        build = (PVM / "scripts/build-kernels.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ -z "${EKS_HOST_CONFIG_SHA256}" ]]', build)
        self.assertIn("sha256sum --check --status", build)
        self.assertIn("validate-kernel-config.py", build)
        self.assertIn("binrpm-pkg", build)
        self.assertIn("vmlinux-pvm", build)
        self.assertIn("manifest.json", build)
        self.assertIn('host_base_config_copy="${work_directory}/eks-host-base.config"', build)
        self.assertIn('guest_base_config_copy="${work_directory}/pvm-guest-base.config"', build)

    def test_dependency_install_keeps_the_ami_curl_provider(self) -> None:
        installer = (PVM / "scripts/install-build-dependencies.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("  curl \\\n", installer)

    def test_host_and_guest_fragments_keep_the_paths_separate(self) -> None:
        host = (PVM / "config/host-pvm.fragment").read_text(encoding="utf-8")
        guest = (PVM / "config/guest-pvm.fragment").read_text(encoding="utf-8")
        self.assertIn("CONFIG_KVM_PVM=m", host)
        self.assertIn("CONFIG_ENA_ETHERNET=m", host)
        self.assertNotIn("CONFIG_PVM_GUEST=y", host)
        self.assertIn("CONFIG_PVM_GUEST=y", guest)
        self.assertIn("CONFIG_X86_PIE=y", guest)
        self.assertIn("CONFIG_PAGE_TABLE_ISOLATION=y", guest)
        self.assertIn("CONFIG_MODULES=n", guest)

    def test_config_validator_accepts_complete_synthetic_configs(self) -> None:
        validator = PVM / "scripts/validate-kernel-config.py"
        host_values = {
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
            "CONFIG_BLK_DEV_NVME": "m",
            "CONFIG_ENA_ETHERNET": "m",
            "CONFIG_XFS_FS": "m",
            "CONFIG_EXT4_FS": "y",
            "CONFIG_OVERLAY_FS": "m",
            "CONFIG_NF_CONNTRACK": "m",
            "CONFIG_VHOST_NET": "m",
            "CONFIG_VHOST_VSOCK": "m",
            "CONFIG_VIRTIO_VSOCKETS": "m",
        }
        guest_values = {
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

        with tempfile.TemporaryDirectory() as directory:
            for role, values in (("host", host_values), ("guest", guest_values)):
                path = Path(directory) / f"{role}.config"
                path.write_text(
                    "\n".join(
                        f"{name}={value}" if value != "n" else f"# {name} is not set"
                        for name, value in values.items()
                    )
                    + "\n",
                    encoding="utf-8",
                )
                subprocess.run(
                    ["python3", str(validator), "--role", role, "--config", str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

    def test_config_validator_rejects_missing_pvm_support(self) -> None:
        validator = PVM / "scripts/validate-kernel-config.py"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.config"
            path.write_text("CONFIG_KVM=m\n# CONFIG_KVM_PVM is not set\n", encoding="utf-8")
            result = subprocess.run(
                ["python3", str(validator), "--role", "host", "--config", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_KVM_PVM must be m", result.stdout)


if __name__ == "__main__":
    unittest.main()
