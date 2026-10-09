#!/usr/bin/env bash
# Print an access token from the dev stack's Keycloak for a dev-realm user, for
# trying the admin API by hand: curl -H "Authorization: Bearer $(scripts/dev-token.sh bob)".
# Development-only credentials; this works only against the dev realm.
# Usage: scripts/dev-token.sh [alice|bob]   (default bob)
#   alice has TOTP, so her direct grant includes a code computed from her dev seed.
#   Direct grants give acr=pwd. An acr=mfa token needs the browser flow
#   (see controlplane tests).
set -euo pipefail

who=${1:-bob}
base=${KEYCLOAK_URL:-http://127.0.0.1:58080}
case "$who" in
  alice|bob) ;;
  *) echo "usage: $0 [alice|bob]" >&2; exit 2 ;;
esac

args=(-d grant_type=password -d client_id=purser-dev-test
      -d "username=$who@acme-dev.example" -d "password=$who-dev-only-not-a-secret")
if [ "$who" = alice ]; then
  totp=$(python3 -c '
import hashlib, hmac, struct, time
d = hmac.new(b"alice-totp-dev-only-not-a-secret", struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
o = d[-1] & 15
print("%06d" % ((struct.unpack(">I", d[o:o + 4])[0] & 0x7FFFFFFF) % 1000000))')
  args+=(-d "totp=$totp")
fi

curl -fsS --noproxy '*' "${args[@]}" "$base/realms/purser-dev/protocol/openid-connect/token" |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])'
