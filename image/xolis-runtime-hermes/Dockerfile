FROM public.ecr.aws/docker/library/python:3.12-slim

ARG HERMES_COMMIT=846b14ab01a84483d2c3dd429579173040474585
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/workspace \
    HERMES_HOME=/workspace/.hermes
WORKDIR /opt/xolis-runtime

RUN apt-get update >/dev/null \
    && apt-get install --yes --no-install-recommends ca-certificates curl git ripgrep >/dev/null \
    && rm -rf /var/lib/apt/lists/*

COPY services/xolis-runtime-python/pyproject.toml ./
COPY services/xolis-runtime-python/src/ src/
RUN git init /opt/hermes-agent \
    && git -C /opt/hermes-agent remote add origin https://github.com/NousResearch/hermes-agent.git \
    && git -C /opt/hermes-agent fetch --depth 1 origin "${HERMES_COMMIT}" \
    && git -C /opt/hermes-agent checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/hermes-agent rev-parse HEAD)" = "${HERMES_COMMIT}" \
    && (pip install --no-cache-dir --editable /opt/hermes-agent . \
        > /tmp/hermes-install.log 2>&1 \
        || { tail -n 120 /tmp/hermes-install.log >&2; exit 1; }) \
    && hermes --help >/dev/null \
    && rm /tmp/hermes-install.log \
    && groupadd --gid 1000 xolis \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin xolis \
    && install -d -m 0750 -o 1000 -g 1000 /workspace

USER 1000:1000
EXPOSE 8888
ENTRYPOINT ["uvicorn", "xolis_runtime.app:app", "--host", "0.0.0.0", "--port", "8888"]
