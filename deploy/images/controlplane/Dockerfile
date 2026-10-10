# Purser control-plane API image (architecture §9, D2).
#
# Build from the repository root:
#   docker build -f deploy/images/controlplane/Dockerfile -t purser-controlplane .
# (or `make cp-image`). In a Claude Code cloud session the build needs the
# egress CA to reach PyPI; it is passed as a BuildKit secret, which is mounted
# for that one RUN step and never stored in a layer.
#
# The runtime stage is distroless: no shell, no package manager, no pip;
# Python 3.13 runs as the nonroot user (UID 65532). Run it with a read-only
# root filesystem and a tmpfs at /tmp (deploy/compose/dev.yaml does).
# No secret is ever an ARG or ENV here: configuration arrives at run time.

FROM public.ecr.aws/docker/library/python:3.13.16-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS build

WORKDIR /build
COPY controlplane/requirements.lock ./
# Wheels only, every one hash-checked against the lock.
RUN --mount=type=secret,id=egress-ca,required=false \
    if [ -s /run/secrets/egress-ca ]; then export PIP_CERT=/run/secrets/egress-ca; fi; \
    python -m pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore \
      --no-deps --require-hashes --only-binary=:all: --target /app/site -r requirements.lock

COPY controlplane/src/purser_controlplane /app/purser_controlplane
COPY controlplane/alembic.ini /app/alembic.ini
COPY controlplane/alembic /app/alembic
# Precompile, since the runtime filesystem is read-only.
RUN python -m compileall -q -j 0 /app

FROM gcr.io/distroless/python3-debian13:nonroot@sha256:83aa8d4f74a4d7f7cf2d472054139bef71a927b76c680c0f2e1021d6b1d6d732

COPY --from=build /app /app
ENV PYTHONPATH=/app/site:/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PURSER_ALEMBIC_INI=/app/alembic.ini
WORKDIR /app
USER 65532:65532
EXPOSE 8080
# The base image's entrypoint is the Python interpreter.
CMD ["-m", "purser_controlplane", "serve"]
