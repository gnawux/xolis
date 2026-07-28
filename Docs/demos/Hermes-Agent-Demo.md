# Hermes Agent Interactive Demo

This opt-in demo runs Hermes Agent in a dedicated sandbox profile. It does not
change the ordinary `python-basic-v1` profile. The image pins upstream Hermes
Agent commit `846b14ab01a84483d2c3dd429579173040474585` (version 0.19.0 at the
time of evaluation).

Validated immutable references from the 2026-07-28 AWS lab run are:

- OCI: `479874045111.dkr.ecr.ap-northeast-1.amazonaws.com/xolis/xolis-runtime-hermes@sha256:7c5c5e5dbbc11f958475c3b696932f5daf1bc93506bb671a281c8b5c28194568`
- Nydus: `479874045111.dkr.ecr.ap-northeast-1.amazonaws.com/xolis/xolis-runtime-hermes@sha256:4c8e52cb7ab790304d326fb1d952219e4a596f4ec111f024b04382cbd843f0c5`
- API: `479874045111.dkr.ecr.ap-northeast-1.amazonaws.com/xolis/xolis-api@sha256:c92e4ad57456b8310722540732f61ba9047d29b525266af16318bec4619db1ae`

The Nydus profile was validated on AMI `ami-0ec0906871c3a9d9b` without model
configuration. `hermes --help`, buffered commands, ordered SSE stdout/stderr,
WebSocket/PTTY input and output, file operations, egress denial, and foreground
cleanup passed. This confirms the sandbox and interaction path only; it does
not confirm an inference provider or a credentialed agent task.

## Prepare the Profile

Build and publish the images with `tools/xolis_image_builder.py`. Take the
immutable `xolis-runtime-hermes` and `xolis-runtime-hermes-nydus` references
from its JSON output and render the profiles:

    python3 tools/render_hermes_profile.py \
        --image-reference ACCOUNT.dkr.ecr.ap-northeast-1.amazonaws.com/xolis/xolis-runtime-hermes@sha256:DIGEST \
        > /tmp/hermes-profile.yaml

    python3 tools/render_hermes_profile.py \
        --image-mode nydus \
        --image-reference ACCOUNT.dkr.ecr.ap-northeast-1.amazonaws.com/xolis/xolis-runtime-hermes@sha256:NYDUS_DIGEST \
        > /tmp/hermes-nydus-profile.yaml

Create `Secret/hermes-agent-credentials` in `xolis-sandboxes` with only the
provider variables required for the selected model. Never store that Secret or
its values in this repository. The profile passes the Secret at runtime; the
image contains no credentials.

The checked-in profile permits DNS but intentionally denies external model
traffic. Before the demo, add a reviewed CNI policy that allows TCP 443 only to
the selected provider endpoints. Do not use an unrestricted Internet egress
rule. Then apply the rendered profile and configure `xolis-api` to use
`XOLIS_PROFILE=hermes-agent-v1`, `XOLIS_WARM_POOL=hermes-agent-v1-pool`, and a
command timeout of at least 900 seconds. For Nydus, use
`hermes-agent-nydus-v1`, `hermes-agent-nydus-v1-pool`, and install the opt-in
`xolis-kata-nydus` RuntimeClass before applying the profile.

## Run the Demo

Create a sandbox, connect to
`/v1/sandboxes/{id}/sessions` with the tenant header, and send this first
WebSocket message:

    {"type":"start","command":"hermes","ttl_seconds":900,"rows":30,"columns":120}

Send terminal keystrokes as base64-encoded `input` messages and render decoded
`output` messages. Use `resize`, `cancel`, and `close` as documented in the
runtime README.

Give Hermes this deterministic task:

> In `/workspace/demo`, create `status.md` with a short English summary of this
> sandbox session and create `result.json` containing the keys `agent`,
> `task`, and `completed`. Set `agent` to `hermes`, `task` to
> `interactive-sandbox-demo`, and `completed` to `true`. Do not read or print
> environment variables or credentials. Finally, display both files.

After the run, download and inspect both artifacts through the file API. Close
the WebSocket and delete the sandbox. Confirm that no command remains running
and that the sandbox claim is deleted. The demo is a correctness demonstration,
not a performance result.

In the bounded 2026-07-28 comparison, the Nydus image pull took 0.585 seconds
and the OCI pull took 4.696 seconds, while end-to-end Ready took 16.601 seconds
for Nydus and 10.415 seconds for OCI. There was one sample per mode and Nydus
ran first. Treat these numbers as diagnostic evidence, not a performance claim.
