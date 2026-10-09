#!/usr/bin/env bash
# Smoke-check the dev stack's Keycloak (ADR 0003) from the host:
#   - the purser-dev discovery document names the fixed issuer;
#   - a test token carries the admin-API audience, the subject, and the
#     acme-dev organization ID, and no claim outside an allowlist of
#     identifiers, levels, and token metadata;
#   - the control plane's admin-API client can read the realm, which has admin
#     events saved with 7-day retention and without representations;
#   - the second realm is a different issuer with different keys.
# Uses only the dev realm's development-only credentials.
# Usage: scripts/check-dev-keycloak.sh [base-url]   (default http://127.0.0.1:58080)
set -euo pipefail

base=${1:-http://127.0.0.1:58080}
realm=purser-dev
issuer=http://localhost:58080/realms/$realm
org_id=ac3e0000-0000-4000-8000-000000000001
bob_id=b0b00000-0000-4000-8000-000000000002

fail() { echo "FAIL: $*" >&2; exit 1; }
# The scripts below read JSON from stdin and get their arguments from argv.
json() { python3 -c "$1" "${@:2}"; }

token() { # realm client username password
  curl -fsS --noproxy '*' -d grant_type=password -d "client_id=$2" -d "username=$3" -d "password=$4" \
    "$base/realms/$1/protocol/openid-connect/token" | json 'import json,sys; print(json.load(sys.stdin)["access_token"])'
}

claims() { # token -> JSON of header and payload
  json '
import base64, json, sys
h, p, _ = sys.argv[1].split(".")
dec = lambda s: json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
print(json.dumps({"header": dec(h), "claims": dec(p)}))' "$1"
}

echo "== discovery"
disc=$(curl -fsS --noproxy '*' "$base/realms/$realm/.well-known/openid-configuration")
got=$(json 'import json,sys; print(json.load(sys.stdin)["issuer"])' <<<"$disc")
[ "$got" = "$issuer" ] || fail "issuer is $got, expected $issuer"
echo "ok: issuer $got"

echo "== test token (bob, purser-dev-test)"
bob_tok=$(token "$realm" purser-dev-test bob@acme-dev.example bob-dev-only-not-a-secret)
claims "$bob_tok" | json '
import json, sys
d = json.load(sys.stdin); h, c = d["header"], d["claims"]
iss, org_id, sub = sys.argv[1:4]
aud = c["aud"] if isinstance(c["aud"], list) else [c["aud"]]
assert h["alg"] == "RS256", h
assert c["iss"] == iss, c["iss"]
assert "purser-admin-api" in aud, aud
assert c["sub"] == sub, c["sub"]
assert c["acr"] == "pwd", c["acr"]
assert c["organization"] == {"acme-dev": {"id": org_id}}, c["organization"]
# Access tokens carry identifiers, levels, and token metadata only. An allowlist,
# so a new profile or role claim fails here. (auth_time appears on browser logins.)
allowed = {"iss", "sub", "aud", "azp", "acr", "auth_time", "organization", "scope", "sid",
           "allowed-origins", "exp", "iat", "jti", "typ"}
extra = set(c) - allowed
assert not extra, f"unexpected access-token claims: {sorted(extra)}"
print("ok: aud, sub, acr=pwd, organization id; only allowlisted claims")' "$issuer" "$org_id" "$bob_id"

echo "== audience absent on purser-dev-noaud"
noaud_tok=$(token "$realm" purser-dev-noaud bob@acme-dev.example bob-dev-only-not-a-secret)
claims "$noaud_tok" | json '
import json, sys
c = json.load(sys.stdin)["claims"]
assert "purser-admin-api" not in str(c.get("aud")), c.get("aud")
print("ok: no purser-admin-api audience")'

echo "== admin-API client (purser-controlplane)"
sa=$(curl -fsS --noproxy '*' -d grant_type=client_credentials -d client_id=purser-controlplane \
  -d client_secret=controlplane-dev-only-not-a-secret \
  "$base/realms/$realm/protocol/openid-connect/token" | json 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -fsS --noproxy '*' -H "Authorization: Bearer $sa" "$base/admin/realms/$realm" | json '
import json, sys
r = json.load(sys.stdin)
assert r["adminEventsEnabled"] is True, "admin events not saved"
assert r["adminEventsDetailsEnabled"] is False, "admin events include representations"
assert r["attributes"].get("adminEventsExpiration") == "604800", r["attributes"].get("adminEventsExpiration")
assert r["organizationsEnabled"] is True
print("ok: admin events saved, 7-day retention, no representations")'
code=$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $sa" "$base/admin/realms/$realm/clients")
[ "$code" = 403 ] || fail "admin-API client can list clients (HTTP $code); it should not"
echo "ok: admin-API client cannot list clients"

echo "== second realm is a different issuer with different keys"
other=$(token purser-dev-other purser-dev-test carol@other-dev.example carol-dev-only-not-a-secret)
json '
import base64, json, sys
dec = lambda t, i: json.loads(base64.urlsafe_b64decode(t.split(".")[i] + "=" * (-len(t.split(".")[i]) % 4)))
a, b = sys.argv[1], sys.argv[2]
assert dec(a, 1)["iss"] != dec(b, 1)["iss"]
assert dec(a, 0)["kid"] != dec(b, 0)["kid"]
print("ok:", dec(b, 1)["iss"])' "$bob_tok" "$other" </dev/null

echo "Keycloak dev realm checks passed."
