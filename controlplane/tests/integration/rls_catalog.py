"""The reviewed row-level-security allowlist and the A5 catalog check (spec 4, A5 part 3).

rls_violations() reads the catalog and lists every way the schema breaks A5's
rules. test_rls_catalog.py asserts it is empty on the migrated schema and
that it catches planted violations; test_rls.py takes its table list from here.
"""

from collections.abc import Mapping

from sqlalchemy import Connection, text

SCHEMA = "controlplane"

# The reviewed allowlist: tables without row-level security, each with its reason.
RLS_ALLOWLIST: Mapping[str, str] = {
    "platform_operators": "Purser staff, not any org's data; only the definer role reads it",
    "alembic_version": "the schema's migration version; no org data",
}
# The only org-data tables the allowlist may hold: the reservation ledger's
# (spec 4), which Lane B adds with its own explicit-org functions.
LEDGER_TABLES: frozenset[str] = frozenset()

# The request path: member routes. Its policies must key on the request's org.
REQUEST_PATH_ROLES = ("purser_cp_app",)
# Cross-org work runs on these, never on the request path's role (spec 4).
CROSS_ORG_ROLES = ("purser_cp_operator", "purser_cp_sweeper")
ORG_KEY = "current_org_id()"
PUBLIC = 0


def org_data_tables(conn: Connection) -> set[str]:
    """Tables with an org ID column, or a foreign key into one (spec 4), and orgs itself."""
    tables = set(
        conn.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = :s AND c.relkind IN ('r', 'p')"
            ),
            {"s": SCHEMA},
        ).scalars()
    )
    org_data = {"orgs"} & tables
    org_data |= set(
        conn.execute(
            text(
                "SELECT c.relname FROM pg_attribute a"
                " JOIN pg_class c ON c.oid = a.attrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = :s AND c.relkind IN ('r', 'p')"
                " AND a.attname = 'org_id' AND NOT a.attisdropped"
            ),
            {"s": SCHEMA},
        ).scalars()
    )
    references = conn.execute(
        text(
            "SELECT src.relname, dst.relname FROM pg_constraint k"
            " JOIN pg_class src ON src.oid = k.conrelid"
            " JOIN pg_class dst ON dst.oid = k.confrelid"
            " JOIN pg_namespace n ON n.oid = src.relnamespace"
            " WHERE k.contype = 'f' AND n.nspname = :s"
        ),
        {"s": SCHEMA},
    ).all()
    changed = True
    while changed:
        changed = False
        for source, target in references:
            if target in org_data and source not in org_data:
                org_data.add(source)
                changed = True
    return org_data


def rls_violations(
    conn: Connection,
    allowlist: Mapping[str, str] = RLS_ALLOWLIST,
    ledger: frozenset[str] = LEDGER_TABLES,
) -> list[str]:
    """Every way the catalog breaks A5's rules; empty when it complies."""
    found: list[str] = []
    # .all(): a list of pairs. dict() would take a Result, which has keys(), for a mapping.
    roles: dict[str, int] = dict(
        conn.execute(
            text("SELECT rolname, oid FROM pg_roles WHERE rolname = ANY(:r)"),
            {"r": [*REQUEST_PATH_ROLES, *CROSS_ORG_ROLES]},
        ).all()
    )
    for role in (*REQUEST_PATH_ROLES, *CROSS_ORG_ROLES):
        if role not in roles:
            found.append(f"role {role} does not exist")
    request_path = {roles[r] for r in REQUEST_PATH_ROLES if r in roles}

    policies = conn.execute(
        text(
            "SELECT c.relname, p.polname, p.polcmd::text, p.polpermissive, p.polroles::oid[],"
            " pg_get_expr(p.polqual, p.polrelid), pg_get_expr(p.polwithcheck, p.polrelid)"
            " FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid"
            " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :s"
        ),
        {"s": SCHEMA},
    ).all()

    # Tables: forced RLS and an org-keyed policy for reads and writes, unless allowlisted.
    tables = conn.execute(
        text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = :s AND c.relkind IN ('r', 'p')"
        ),
        {"s": SCHEMA},
    ).all()
    org_data = org_data_tables(conn)
    for table, enabled, forced in tables:
        if table in allowlist:
            if table in org_data and table not in ledger:
                found.append(f"{table}: holds org data but is on the allowlist")
            continue
        if not (enabled and forced):
            found.append(f"{table}: row-level security is not enabled and forced")
        keyed = [
            p
            for p in policies
            if p[0] == table
            and p[2] == "*"
            and p[3]
            and request_path <= set(p[4])
            and ORG_KEY in (p[5] or "")
            and ORG_KEY in (p[6] or "")
        ]
        if not keyed:
            found.append(f"{table}: no policy keys the request path's reads and writes on its org")

    # The request path's permissive policies see one org only.
    for table, name, _, permissive, policy_roles, using, check in policies:
        applies = PUBLIC in policy_roles or bool(request_path & set(policy_roles))
        exprs = [e for e in (using, check) if e is not None]
        if applies and permissive and (not exprs or any(ORG_KEY not in e for e in exprs)):
            found.append(f"{table}.{name}: lets the request path's role past its org")

    # Request-path and cross-org roles own nothing, inherit nothing, bypass nothing.
    for role, oid in roles.items():
        row = conn.execute(
            text(
                "SELECT r.rolsuper, r.rolbypassrls,"
                " (SELECT count(*) FROM pg_class WHERE relowner = r.oid)"
                " + (SELECT count(*) FROM pg_proc WHERE proowner = r.oid)"
                " + (SELECT count(*) FROM pg_namespace WHERE nspowner = r.oid)"
                " + (SELECT count(*) FROM pg_type WHERE typowner = r.oid),"
                " (SELECT count(*) FROM pg_auth_members WHERE member = r.oid)"
                " FROM pg_roles r WHERE r.oid = :o"
            ),
            {"o": oid},
        ).one()
        superuser, bypass, owned, memberships = row
        if superuser or bypass:
            found.append(f"role {role}: superuser or BYPASSRLS")
        if owned:
            found.append(f"role {role}: owns {owned} object(s)")
        if memberships:
            found.append(f"role {role}: is a member of another role")

    # SECURITY DEFINER functions: a fixed, safe search_path; a non-login owner.
    for name, config, can_login in conn.execute(
        text(
            "SELECT n.nspname || '.' || p.proname, p.proconfig, o.rolcanlogin"
            " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
            " JOIN pg_roles o ON o.oid = p.proowner"
            " WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
        )
    ):
        paths = [c for c in (config or []) if c.startswith("search_path=")]
        if not paths or any("public" in p or "$user" in p for p in paths):
            found.append(f"function {name}: SECURITY DEFINER without a fixed search_path")
        if can_login:
            found.append(f"function {name}: SECURITY DEFINER owned by a role that can log in")
    return found
