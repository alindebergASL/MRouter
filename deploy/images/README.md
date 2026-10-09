# deploy/images/

One Dockerfile per image: relay/sidecar, control plane, console, mock providers. Each lands in the
same pull request as its component's first code.

Rules (architecture §9, D2): base images from `public.ecr.aws/docker/library/…` or distroless from
gcr.io, pinned by digest; multi-arch (`linux/amd64`, `linux/arm64`); non-root; read-only root
filesystem with declared `tmpfs` mounts only; no shell or package manager at runtime; no
credentials in any layer; the image holds the same binary as the native release (D1).

Nothing here yet.
