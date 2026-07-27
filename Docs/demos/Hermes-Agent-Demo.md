# Hermes Agent Interactive Demo

This opt-in demo runs Hermes Agent in a dedicated sandbox profile. It does not
change the ordinary `python-basic-v1` profile. The image pins upstream Hermes
Agent commit `846b14ab01a84483d2c3dd429579173040474585` (version 0.19.0 at the
time of evaluation).

## Prepare the Profile

Build and publish the images with `tools/xolis_image_builder.py`. Take the
immutable `xolis-runtime-hermes` reference from its JSON output and render the
profile:

    python3 tools/render_hermes_profile.py \
        --image-reference ACCOUNT.dkr.ecr.ap-northeast-1.amazonaws.com/xolis/xolis-runtime-hermes@sha256:DIGEST \
        > /tmp/hermes-profile.yaml

Create `Secret/hermes-agent-credentials` in `xolis-sandboxes` with only the
provider variables required for the selected model. Never store that Secret or
its values in this repository. The profile passes the Secret at runtime; the
image contains no credentials.

The checked-in profile permits DNS but intentionally denies external model
traffic. Before the demo, add a reviewed CNI policy that allows TCP 443 only to
the selected provider endpoints. Do not use an unrestricted Internet egress
rule. Then apply the rendered profile and configure `xolis-api` to use
`XOLIS_PROFILE=hermes-agent-v1`, `XOLIS_WARM_POOL=hermes-agent-v1-pool`, and a
command timeout of at least 900 seconds.

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
