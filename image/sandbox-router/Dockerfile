FROM public.ecr.aws/docker/library/golang:1.26.4-bookworm AS builder

ARG AGENT_SANDBOX_COMMIT=7935857fee859bb18752ee04d8948b975e47ff20
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /source
RUN git init \
    && git remote add origin https://github.com/kubernetes-sigs/agent-sandbox.git \
    && git fetch --depth 1 origin "${AGENT_SANDBOX_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${AGENT_SANDBOX_COMMIT}"
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath \
    -ldflags='-s -w' -o /sandbox-router ./sandbox-router/cmd

FROM public.ecr.aws/docker/library/debian:bookworm-slim

RUN groupadd --gid 65532 xolis \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin xolis
COPY --from=builder /sandbox-router /usr/local/bin/sandbox-router

USER 65532:65532
EXPOSE 8080 8081 9090
ENTRYPOINT ["/usr/local/bin/sandbox-router"]
