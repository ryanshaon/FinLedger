# syntax=docker/dockerfile:1
# FinLedger runtime image: Person 2 platform (API, ingest worker, outbox worker, migrations) + Person 3 control UI.
# One image, the command picks the process:
#   finledger-platform serve --host 0.0.0.0 --port 8000
#   finledger-platform worker
#   finledger-platform outbox-worker --client-id <uuid>
#   finledger-control --host 0.0.0.0 --port 8770
#   finledger-platform migrate            (one-off, owner DSN only)
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY person2_platform/pyproject.toml person2_platform/uv.lock person2_platform/
# Optional `--secret id=extra_ca,src=<pem>` for builds behind a TLS-intercepting proxy; never baked into the image.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --project person2_platform --locked --no-dev --extra s3 --no-install-project
COPY person2_platform person2_platform
COPY person3_control_ui person3_control_ui
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --project person2_platform --locked --no-dev --extra s3 --no-editable \
 && uv pip install --python /opt/venv/bin/python --no-deps ./person3_control_ui

FROM python:3.12-slim
RUN useradd --system --uid 10001 --create-home finledger
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 FINLEDGER_STORE_ROOT=/var/lib/finledger/objects
RUN mkdir -p /var/lib/finledger/objects && chown -R finledger /var/lib/finledger
USER finledger
WORKDIR /home/finledger
EXPOSE 8000 8770
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz',timeout=4).status==200 else 1)"
CMD ["finledger-platform", "serve", "--host", "0.0.0.0", "--port", "8000"]
