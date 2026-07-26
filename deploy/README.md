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
