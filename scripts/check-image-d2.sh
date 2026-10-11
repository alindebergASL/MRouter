#!/usr/bin/env bash
# D2 checks for one built image (architecture §9): runs as a non-zero UID; has
# no shell and no package manager; serves its health and readiness endpoints
# with a read-only root filesystem, a tmpfs at /tmp only, and no
# capabilities, writing nothing to its own layers; and carries no build-time
# canary or egress CA in any layer. SBOM, signature, and the vulnerability
# gate are Lane A's (build plan §5).
#
# Usage: scripts/check-image-d2.sh <image> [canary]
#   Needs the dev stack (make dev-up) for the readiness check.
#   canary: a string planted in the build context and environment before the
#   build (make cp-image does both when PURSER_BUILD_CANARY is set) that must
#   not appear in any layer.
set -euo pipefail

image=${1:?usage: $0 <image> [canary]}
canary=${2:-}
network=${D2_NETWORK:-purser-dev_default}
port=${D2_PORT:-58181}
name=purser-d2-check-$$

fail() { echo "FAIL: $*" >&2; exit 1; }
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== non-root"
user=$(docker image inspect -f '{{.Config.User}}' "$image")
uid=${user%%:*}
case "$uid" in
  ""|0|root) fail "image runs as '${user:-root}'" ;;
esac
echo "ok: runs as $user"

echo "== no shell, no package manager"
for shell in /bin/sh /bin/bash /bin/ash /busybox/sh; do
  if docker run --rm --entrypoint "$shell" "$image" -c true >/dev/null 2>&1; then
    fail "$shell runs in the image"
  fi
done
docker run --rm --entrypoint /usr/bin/python3 "$image" -c '
import importlib.util, os, sys
paths = ["/bin/sh", "/bin/bash", "/bin/dash", "/usr/bin/apt", "/usr/bin/apt-get", "/usr/bin/dpkg",
         "/sbin/apk", "/usr/bin/pip", "/usr/bin/pip3", "/usr/local/bin/pip", "/usr/bin/curl", "/usr/bin/wget"]
found = [p for p in paths if os.path.exists(p)]
if importlib.util.find_spec("pip"):
    found.append("pip module")
# Debian keeps ensurepip as a stub with no bundled wheel; a pip wheel anywhere
# would make pip runnable straight from the zip.
import glob
found += glob.glob("/usr/lib/python3*/ensurepip/_bundled/*.whl") + glob.glob("/usr/share/python-wheels/*.whl")
if found:
    sys.exit(f"present: {found}")
' || fail "a shell, package manager, or downloader is present"
# And it can't be bootstrapped, even into a writable tmpfs.
if docker run --rm --read-only --tmpfs /tmp --entrypoint /usr/bin/python3 "$image" \
     -m ensurepip --root /tmp/pip >/dev/null 2>&1; then
  fail "ensurepip can install pip"
fi
echo "ok: no shell, package manager, pip (nor a way to bootstrap it), or downloader"

echo "== read-only root filesystem, tmpfs at /tmp only, no capabilities"
docker run -d --name "$name" --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges --network "$network" -p "127.0.0.1:$port:8080" \
  -e PURSER_ENV=dev \
  -e PURSER_DB_URL=postgresql+psycopg://purser_cp_app:cp-app-dev-only-not-a-secret@postgres:5432/purser_dev \
  -e PURSER_DB_OPERATOR_URL=postgresql+psycopg://purser_cp_operator:cp-operator-dev-only-not-a-secret@postgres:5432/purser_dev \
  -e PURSER_OIDC_ISSUER=http://localhost:58080/realms/purser-dev \
  -e PURSER_OIDC_DISCOVERY_URL=http://keycloak:58080/realms/purser-dev/.well-known/openid-configuration \
  "$image" >/dev/null
for _ in $(seq 1 60); do
  curl -fsS --noproxy '*' "http://127.0.0.1:$port/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS --noproxy '*' "http://127.0.0.1:$port/healthz" >/dev/null || { docker logs "$name" >&2; fail "/healthz"; }
ready=$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/readyz")
[ "$ready" = 200 ] || { docker logs "$name" >&2; fail "/readyz returned $ready"; }
changed=$(docker diff "$name")
[ -z "$changed" ] || fail "the container wrote to its layers: $changed"
echo "ok: healthz and readyz 200 read-only; no layer writes"

echo "== no canary or egress CA in any layer"
dir=$(mktemp -d)
docker save "$image" -o "$dir/image.tar"
mkdir "$dir/x"
tar -xf "$dir/image.tar" -C "$dir/x"
patterns=()
if [ -n "$canary" ]; then
  # A short canary would match by chance and prove nothing.
  [ "${#canary}" -ge 16 ] || fail "the canary must be at least 16 characters"
  patterns+=("$canary")
fi
# In a cloud session, the egress interception CA (passed to the build as a
# secret) must not have landed in a layer. Its PEM body is distinctive; the
# names inside it are base64-encoded, so search for body lines, not names.
ca=${D2_CA_FILE:-/root/.ccr/agent-proxy-ca.crt}
if [ -r "$ca" ]; then
  while IFS= read -r line; do patterns+=("$line"); done < <(sed -n '2,4p' "$ca")
else
  echo "note: no egress CA file here; only the canary is searched for"
fi
[ "${#patterns[@]}" -gt 0 ] || { echo "skip: no canary or CA to search for"; patterns=(); }
for blob in "$dir"/x/blobs/sha256/*; do
  for pattern in ${patterns[@]+"${patterns[@]}"}; do
    # Layers are tar or gzip-compressed tar; search both forms. Not grep -q:
    # it exits at the first match, the decompressor then dies of SIGPIPE, and
    # under pipefail the pipeline reads as "not found".
    if { gzip -dc "$blob" 2>/dev/null || cat "$blob"; } | grep -aF -- "$pattern" >/dev/null; then
      rm -rf "$dir"
      fail "found '$pattern' in layer $(basename "$blob")"
    fi
  done
done
rm -rf "$dir"
echo "ok: ${#patterns[@]} patterns absent from $(docker image inspect -f '{{len .RootFS.Layers}}' "$image") layers"

echo "D2 checks passed for $image"
