# Xolis Kubernetes Manifests

The first manifests verify the Kata runtime path before Agent Sandbox is installed:

1. `bootstrap/xolis-runtime.yaml` creates the `xolis-system` namespace and `RuntimeClass/xolis-kata`.
2. `tests/smoke-kata-pod.yaml` schedules a one-shot BusyBox Pod through that RuntimeClass.

The RuntimeClass selects only nodes with `xolis.io/kata-ready=true` and tolerates the dedicated sandbox taint. The self-managed sandbox ASG must use a custom AMI that provides the `xolis-kata` containerd handler before the test can run.

Run the test through the Lab tool after the AMI ID is applied to the sandbox launch template:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json cycle run

The smoke Pod proves the basic RuntimeClass and Kata scheduling path. It does not validate Nydus image lazy loading or Agent Sandbox resources. Add those only after this baseline is repeatable.
