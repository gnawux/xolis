FROM public.ecr.aws/docker/library/python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /opt/xolis-runtime

COPY services/xolis-runtime-python/pyproject.toml ./
COPY services/xolis-runtime-python/src/ src/
RUN pip install --no-cache-dir . \
    && groupadd --gid 1000 xolis \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin xolis \
    && mkdir --mode=0750 /workspace \
    && chown 1000:1000 /workspace

USER 1000:1000
EXPOSE 8888
ENTRYPOINT ["uvicorn", "xolis_runtime.app:app", "--host", "0.0.0.0", "--port", "8888"]
