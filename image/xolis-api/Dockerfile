FROM public.ecr.aws/docker/library/rust:1.95-bookworm AS builder

ENV RUSTUP_TOOLCHAIN=1.95.0
WORKDIR /workspace
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY services/xolis-api/ services/xolis-api/
RUN cargo build --locked --release --package xolis-api

FROM public.ecr.aws/docker/library/debian:bookworm-slim

RUN groupadd --gid 65532 xolis \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin xolis
COPY --from=builder /workspace/target/release/xolis-api /usr/local/bin/xolis-api

USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/xolis-api"]
