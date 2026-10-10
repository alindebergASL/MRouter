# deploy/images/

One Dockerfile per image: relay/sidecar, control plane, console, mock providers. Each lands in the
same pull request as its component's first code.

Rules (architecture §9, D2): base images from `public.ecr.aws/docker/library/…` or distroless from
gcr.io, pinned by digest; multi-arch (`linux/amd64`, `linux/arm64`); non-root; read-only root
filesystem with declared `tmpfs` mounts only; no shell or package manager at runtime; no
credentials in any layer; the image holds the same binary as the native release (D1).

| Image | Dockerfile | Runtime base |
|---|---|---|
| Control plane API | `controlplane/Dockerfile` | `gcr.io/distroless/python3-debian13:nonroot`, pinned |

`make check-pins` also checks every `FROM` line here. `scripts/check-image-d2.sh` runs the D2
checks this lane can run (non-root, no shell or package manager, read-only, no canary in any
layer); SBOMs, signing, and the vulnerability gate are Lane A's.
