FROM public.ecr.aws/docker/library/python:3.13-slim

ARG HERMES_COMMIT=846b14ab01a84483d2c3dd429579173040474585
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/workspace \
    HERMES_HOME=/workspace/.hermes
WORKDIR /opt/xolis-runtime

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl git ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY services/xolis-runtime-python/pyproject.toml ./
COPY services/xolis-runtime-python/src/ src/
RUN pip install --no-cache-dir . \
    && pip install --no-cache-dir \
        "git+https://github.com/NousResearch/hermes-agent.git@${HERMES_COMMIT}" \
    && groupadd --gid 1000 xolis \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin xolis \
    && mkdir --mode=0750 /workspace \
    && chown 1000:1000 /workspace

USER 1000:1000
EXPOSE 8888
ENTRYPOINT ["uvicorn", "xolis_runtime.app:app", "--host", "0.0.0.0", "--port", "8888"]
