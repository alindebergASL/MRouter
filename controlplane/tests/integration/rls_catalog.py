"""The reviewed row-level-security allowlist and the A5 catalog check (spec 4, A5 part 3).

rls_violations() reads the catalog of every non-system schema and lists every
way the database breaks A5's rules. test_rls_catalog.py asserts it is empty on
the migrated schema and that it catches planted violations; test_rls.py takes
its table list from here.

Policies are compared as Postgres prints them back (pg_get_expr), exactly: a
policy that merely mentions the org key, or ORs something onto it, is not
org-keyed.
"""

import re
from collections.abc import Mapping

from sqlalchemy import Connection, text

# The reviewed allowlist: relations without row-level security, each with its reason.
RLS_ALLOWLIST: Mapping[str, str] = {
    "controlplane.platform_operators": (
        "Purser staff, not any org's data; only the definer role reads it"
    ),
    "controlplane.alembic_version": "the schema's migration version; no org data",
}
# The only org-data tables the allowlist may hold: the reservation ledger's
# (spec 4), which Lane B adds with its own explicit-org functions.
LEDGER_TABLES: frozenset[str] = frozenset()

# The request path: member routes. Its policies are the org key and nothing else.
REQUEST_PATH_ROLES = ("purser_cp_app",)
# Cross-org work runs on these, never on the request path's role (spec 4).
OPERATOR_ROLE = "purser_cp_operator"
SWEEPER_ROLE = "purser_cp_sweeper"
CROSS_ORG_ROLES = (OPERATOR_ROLE, SWEEPER_ROLE)
DEFINER_ROLE = "purser_cp_definer"

ORG_KEY = "controlplane.current_org_id()"
ORG_KEY_BODY = "SELECT nullif(current_setting('purser.org_id', true), '')::uuid"
# Every operator-role policy is gated on a registered operator first.
OPERATOR_GATE = "controlplane.current_operator_is_valid()"
# The sweeper's policies, reviewed one by one (0001): it reads identifiers of
# every org and changes only pending rows.
SWEEPER_POLICIES = frozenset(
    {
        ("controlplane.orgs", "sweeper_read"),
        ("controlplane.orgs", "sweeper_finish"),
        ("controlplane.orgs", "sweeper_rollback"),
        ("controlplane.users", "sweeper_read"),
        ("controlplane.users", "sweeper_finish"),
        ("controlplane.users", "sweeper_rollback"),
        ("controlplane.audit_events", "sweeper_audit"),
    }
)
# pg_temp last: otherwise Postgres searches it first for relations.
DEFINER_SEARCH_PATH = "search_path=pg_catalog, pg_temp"
# An org ID column, by name (spec 4: "an org ID column or a foreign key into one").
ORG_ID_COLUMN = re.compile(r"(^|_)(org|organization)_id$")
PUBLIC = 0

_SCHEMAS = (
    "n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')"
    " AND n.nspname NOT LIKE 'pg\\_temp\\_%' AND n.nspname NOT LIKE 'pg\\_toast\\_temp\\_%'"
)


def _relations(conn: Connection) -> list[tuple[str, str, bool, bool, list[str] | None]]:
    """(qualified name, relkind, RLS enabled, RLS forced, reloptions) in non-system schemas."""
    rows = conn.execute(
        text(
            "SELECT n.nspname || '.' || c.relname, c.relkind::text, c.relrowsecurity,"
            " c.relforcerowsecurity, c.reloptions"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            f" WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND {_SCHEMAS}"
        )
    ).all()
    return [tuple(row) for row in rows]  # type: ignore[misc]


def org_data_tables(conn: Connection) -> dict[str, str]:
    """Org-data relations and the column holding each row's org (spec 4).

    An org ID column, or a foreign key into an org-data table (the referencing
    column then holds the org only indirectly, shown as ""). The orgs table is
    its own org.
    """
    columns = conn.execute(
        text(
            "SELECT n.nspname || '.' || c.relname, a.attname FROM pg_attribute a"
            " JOIN pg_class c ON c.oid = a.attrelid"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            f" WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND {_SCHEMAS}"
            " AND a.attnum > 0 AND NOT a.attisdropped"
        )
    ).all()
    org_data: dict[str, str] = {}
    for relation, column in columns:
        if relation == "controlplane.orgs":
            org_data[relation] = "id"
        elif ORG_ID_COLUMN.search(column) and relation not in org_data:
            org_data[relation] = column
    references = conn.execute(
        text(
            "SELECT sn.nspname || '.' || src.relname, dn.nspname || '.' || dst.relname"
            " FROM pg_constraint k"
            " JOIN pg_class src ON src.oid = k.conrelid"
            " JOIN pg_namespace sn ON sn.oid = src.relnamespace"
            " JOIN pg_class dst ON dst.oid = k.confrelid"
            " JOIN pg_namespace dn ON dn.oid = dst.relnamespace"
            " WHERE k.contype = 'f'"
        )
    ).all()
    changed = True
    while changed:
        changed = False
        for source, target in references:
            if target in org_data and source not in org_data:
                org_data[source] = ""
                changed = True
    return org_data


def _role_problems(conn: Connection, role: str, *, may_log_in: bool) -> list[str]:
    row = conn.execute(
        text(
            "SELECT r.rolsuper, r.rolbypassrls, r.rolcanlogin,"
            " (SELECT count(*) FROM pg_class WHERE relowner = r.oid)"
            " + (SELECT count(*) FROM pg_namespace WHERE nspowner = r.oid)"
            " + (SELECT count(*) FROM pg_type WHERE typowner = r.oid),"
            " (SELECT count(*) FROM pg_proc WHERE proowner = r.oid),"
            " (SELECT count(*) FROM pg_auth_members WHERE member = r.oid)"
            " FROM pg_roles r WHERE r.rolname = :r"
        ),
        {"r": role},
    ).one_or_none()
    if row is None:
        return [f"role {role} does not exist"]
    superuser, bypass, can_login, owned, functions, memberships = row
    found = []
    if superuser or bypass:
        found.append(f"role {role}: superuser or BYPASSRLS")
    if can_login and not may_log_in:
        found.append(f"role {role}: can log in")
    if owned or (functions and may_log_in):
        found.append(f"role {role}: owns {owned + functions} object(s)")
    if memberships:
        found.append(f"role {role}: is a member of another role")
    return found


def rls_violations(
    conn: Connection,
    allowlist: Mapping[str, str] = RLS_ALLOWLIST,
    ledger: frozenset[str] = LEDGER_TABLES,
) -> list[str]:
    """Every way the catalog breaks A5's rules; empty when it complies."""
    found: list[str] = []
    for role in (*REQUEST_PATH_ROLES, *CROSS_ORG_ROLES):
        found += _role_problems(conn, role, may_log_in=True)
    found += _role_problems(conn, DEFINER_ROLE, may_log_in=False)
    oids = dict(
        conn.execute(
            text("SELECT rolname, oid FROM pg_roles WHERE rolname = ANY(:r)"),
            {"r": [*REQUEST_PATH_ROLES, *CROSS_ORG_ROLES]},
        ).all()
    )
    request_path = {oids[r] for r in REQUEST_PATH_ROLES if r in oids}

    policies = conn.execute(
        text(
            "SELECT n.nspname || '.' || c.relname, p.polname, p.polcmd::text, p.polpermissive,"
            " p.polroles::oid[], pg_get_expr(p.polqual, p.polrelid),"
            " pg_get_expr(p.polwithcheck, p.polrelid)"
            " FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid"
            f" JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_SCHEMAS}"
        )
    ).all()
    org_data = org_data_tables(conn)

    def org_keyed(relation: str, expression: str | None) -> bool:
        column = org_data.get(relation)
        return bool(column) and expression == f"({column} = {ORG_KEY})"

    # Relations: forced RLS with an org-keyed policy for reads and writes,
    # unless allowlisted. Views, materialized views and foreign tables have no
    # RLS of their own: a view runs as its owner unless it is security_invoker.
    for relation, kind, enabled, forced, options in _relations(conn):
        if kind in ("v", "m", "f"):
            if not (kind == "v" and "security_invoker=true" in (options or [])):
                found.append(
                    f"{relation}: a view or foreign table that bypasses row-level security"
                )
            continue
        if relation in allowlist:
            if relation in org_data and relation not in ledger:
                found.append(f"{relation}: holds org data but is on the allowlist")
            continue
        if not (enabled and forced):
            found.append(f"{relation}: row-level security is not enabled and forced")
        if not any(
            p[0] == relation
            and p[2] == "*"
            and p[3]
            and request_path <= set(p[4])
            and org_keyed(relation, p[5])
            and org_keyed(relation, p[6])
            for p in policies
        ):
            found.append(
                f"{relation}: no policy keys the request path's reads and writes on its org"
            )

    # Each role's permissive policies, checked against what that role may do.
    for relation, name, _, permissive, roles, using, check in policies:
        if not permissive:
            continue  # a restrictive policy can only narrow access
        roles = set(roles)
        expressions = [e for e in (using, check) if e is not None]
        if (PUBLIC in roles or request_path & roles) and not (
            expressions and all(org_keyed(relation, e) for e in expressions)
        ):
            found.append(f"{relation}.{name}: lets the request path's role past its org")
        if PUBLIC in roles or oids.get(OPERATOR_ROLE) in roles:
            gated = all(
                e == OPERATOR_GATE or e.startswith(f"({OPERATOR_GATE} AND ") for e in expressions
            )
            if not (expressions and gated):
                found.append(f"{relation}.{name}: an operator-role policy not gated on an operator")
        if oids.get(SWEEPER_ROLE) in roles and (relation, name) not in SWEEPER_POLICIES:
            found.append(f"{relation}.{name}: a sweeper policy not in the reviewed set")

    # The org key itself.
    key = conn.execute(
        text(
            "SELECT p.prosrc, p.prosecdef FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'controlplane' AND p.proname = 'current_org_id' AND p.pronargs = 0"
        )
    ).one_or_none()
    if key is None or " ".join(key[0].split()) != ORG_KEY_BODY or key[1]:
        found.append(f"function {ORG_KEY}: missing or changed")

    # SECURITY DEFINER functions: pg_temp-last search_path, a non-login owner
    # with no other powers, and no EXECUTE for PUBLIC.
    for name, config, owner, can_login, superuser, bypass, memberships, public in conn.execute(
        text(
            "SELECT n.nspname || '.' || p.proname, p.proconfig, o.rolname, o.rolcanlogin,"
            " o.rolsuper, o.rolbypassrls,"
            " (SELECT count(*) FROM pg_auth_members WHERE member = o.oid),"
            " EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a"
            "         WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE')"
            " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
            " JOIN pg_roles o ON o.oid = p.proowner"
            f" WHERE p.prosecdef AND {_SCHEMAS}"
        )
    ):
        if DEFINER_SEARCH_PATH not in (config or []):
            found.append(f"function {name}: SECURITY DEFINER without a fixed search_path")
        if can_login:
            found.append(f"function {name}: SECURITY DEFINER owned by a role that can log in")
        if superuser or bypass or memberships:
            found.append(f"function {name}: SECURITY DEFINER owner {owner} has other powers")
        if public:
            found.append(f"function {name}: SECURITY DEFINER executable by PUBLIC")
    return found
