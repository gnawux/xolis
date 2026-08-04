# Xolis Kubernetes Manifests

The manifests provide the initial Xolis sandbox service stack:

1. `bootstrap/xolis-runtime.yaml` creates the `xolis-system` namespace and
   `RuntimeClass/xolis-kata`.
2. `agent-sandbox/install-v0.5.3.sh` installs the pinned upstream CRDs and
   controller, then constrains the controller to the managed system node.
3. `xolis/` contains the Rust API, Go router, Python runtime profile, zero-size
   warm pool, namespace-scoped RBAC, and default-deny network policies.
4. `tests/smoke-kata-pod.yaml` remains the minimal Kata runtime check.

The RuntimeClass selects only nodes with `xolis.io/kata-ready=true` and tolerates the dedicated sandbox taint. The self-managed sandbox ASG must use a custom AMI that provides the `xolis-kata` containerd handler before the test can run.

Run the minimal RuntimeClass test through the Lab tool after the AMI ID is
applied to the sandbox launch template:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json cycle run

The AWS lab manifests pin the three images to private ECR digests. Rebuild and
update a reference whenever its source changes. Then install the upstream
controller and apply the rendered stack:

    deploy/agent-sandbox/install-v0.5.3.sh
    kubectl apply -k deploy

Validate all local and pinned upstream manifests without a cluster:

    deploy/tests/validate-manifests.sh

This disposable cycle does not install or validate the complete service. After
the stack is deployed and the sandbox ASG has one Ready node, run the
self-cleaning service acceptance test:

    python3 deploy/tests/smoke_service.py

For the normal AWS lab workflow, let the Lab tool perform the complete sequence
and return the sandbox ASG to zero in a guaranteed cleanup path:

    python3 tools/xolis_aws_lab.py --config tools/xolis_aws_lab.json service run

The test opens a temporary local port-forward to `xolis-api`, creates a sandbox,
verifies Kata placement, commands, files, tenant isolation, idempotency, network
egress policy, explicit deletion, and absolute TTL cleanup. It deletes claims it
created even when an assertion fails. Use `--skip-ttl` only for a faster
development check; the full acceptance run includes the TTL backstop.

The smoke Pod proves the basic RuntimeClass and Kata scheduling path. It does
not validate Nydus image lazy loading. The full service validation creates a
claim through `xolis-api`, executes Python, transfers files, checks policy
rejections, and verifies TTL and explicit cleanup.

## Optional Nydus Evaluation Profile

The default `deploy` kustomization remains the validated ordinary-OCI path. An
AMI built with all three `NYDUS_*` Packer variables also installs and enables
the Nydus snapshotter and records its pinned version in
`/etc/xolis/nydus-version`.

Apply the opt-in overlay only with that AMI:

    kubectl apply -k deploy
    kubectl apply -f deploy/bootstrap/xolis-runtime-nydus.yaml
    kubectl apply -f deploy/xolis/python-profile-nydus.yaml

The opt-in add-on adds `RuntimeClass/xolis-kata-nydus`,
`SandboxTemplate/python-nydus-v1`, and a zero-replica Nydus warm pool. It does
not modify `RuntimeClass/xolis-kata` or `SandboxTemplate/python-basic-v1`, so an
operator can return to ordinary OCI without rebuilding the service stack.

On containerd 2.2, the sandbox kubelet must enable
`RuntimeClassInImageCriApi`; otherwise Nydus media types are sent to the default
overlayfs snapshotter. The validated AMI `ami-0ec0906871c3a9d9b` contains the
required CRI image-service mapping, local-pull compatibility settings, Nydus
ECR credential provider configuration, and RAFS v6 daemon configuration.

## PVM Qualification Add-on

PVM is an independent experimental scheduling path. After enabling
`pvm_ami_id` in `infra/aws/minimal`, apply the PVM RuntimeClass and scale the
PVM ASG without changing the native-KVM ASG:

```console
kubectl apply -f deploy/bootstrap/xolis-runtime-pvm.yaml
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name xolis-lab-pvm \
  --min-size 0 --desired-capacity 1 --max-size 1
kubectl get nodes -l xolis.io/pvm-ready=true -o wide
kubectl apply -f deploy/tests/smoke-pvm-pod.yaml
kubectl wait --for=condition=Ready pod/xolis-kata-pvm-smoke \
  --namespace xolis-system --timeout=5m
```

`RuntimeClass/xolis-kata-pvm` requires the PVM capability labels and tolerates
both the sandbox and PVM taints. It cannot fall back to the native-KVM pool.
The smoke Pod checks guest boot, cluster DNS, and public HTTPS egress. Delete
the Pod and return the PVM ASG to desired capacity zero after qualification.
The AWS NodeConfig must preserve `/etc/containerd/conf.d/*.toml` imports when
nodeadm generates the final containerd configuration; otherwise kubelet reports
that `xolis-kata-pvm` is not configured even though the fragment exists.

For full lifecycle testing, apply `deploy/xolis/python-profile-pvm.yaml` after
Agent Sandbox and the base Xolis deployment are installed. This adds the
`python-pvm-v1` template and a zero-replica warm pool without changing the
stable `python-basic-v1` profile.

## Optional Hermes Agent Profile

The Hermes evaluation uses a separate Python 3.12 image and a zero-replica
profile. Build the image, render `xolis/hermes-profile.yaml.in` with
`tools/render_hermes_profile.py`, and apply only the rendered output. The
renderer requires an immutable private ECR digest. See
`Docs/demos/kubecon-japan-2026-hermes-agent/README.md` for credential, egress,
interactive session, and cleanup requirements. The profile is not part of the
default kustomization.

The 2026-07-28 AWS lab validation used API digest
`sha256:c92e4ad57456b8310722540732f61ba9047d29b525266af16318bec4619db1ae`,
OCI Hermes digest
`sha256:7c5c5e5dbbc11f958475c3b696932f5daf1bc93506bb671a281c8b5c28194568`,
and Nydus Hermes digest
`sha256:4c8e52cb7ab790304d326fb1d952219e4a596f4ec111f024b04382cbd843f0c5`.
Both image modes passed the service smoke test. The Nydus profile additionally
passed `hermes --help`, ordered SSE output, and WebSocket/PTTY input/output
without model configuration.
