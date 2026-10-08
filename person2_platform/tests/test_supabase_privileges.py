"""A Supabase-like public schema must not expose FinLedger's server-only data API."""

from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from finledger_platform.db import migrate


def test_migrations_revoke_data_api_access_and_secure_operational_tables(owner_dsn):
    database = f"finledger_privileges_{uuid4().hex[:12]}"
    created_roles: list[str] = []
    database_created = False
    try:
        with psycopg.connect(owner_dsn, autocommit=True) as admin:
            for role in ("anon", "authenticated", "service_role"):
                exists = admin.execute("select exists (select from pg_roles where rolname = %s)", (role,)).fetchone()[0]
                if not exists:
                    admin.execute(sql.SQL("create role {} nologin").format(sql.Identifier(role)))
                    created_roles.append(role)
            admin.execute(sql.SQL("create database {}").format(sql.Identifier(database)))
            database_created = True

        details = conninfo_to_dict(owner_dsn)
        details["dbname"] = database
        test_dsn = make_conninfo(**details)
        with psycopg.connect(test_dsn, autocommit=True) as conn:
            for role in ("anon", "authenticated", "service_role"):
                conn.execute(
                    sql.SQL("alter default privileges for role postgres in schema public "
                            "grant select, insert, update, delete on tables to {}")
                    .format(sql.Identifier(role))
                )
                conn.execute(
                    sql.SQL("alter default privileges for role postgres in schema public "
                            "grant execute on functions to {}")
                    .format(sql.Identifier(role))
                )
        migrate(test_dsn)
        with psycopg.connect(test_dsn, autocommit=True) as conn:
            for role in ("anon", "authenticated", "service_role"):
                for table in ("users", "documents", "rate_limits", "schema_migrations"):
                    allowed = conn.execute(
                        "select has_table_privilege(%s, %s, 'SELECT')", (role, f"public.{table}")
                    ).fetchone()[0]
                    assert not allowed, f"{role} can read {table}"
                for function in ("auth_user(bytea)", "claim_job(text,integer)"):
                    allowed = conn.execute(
                        "select has_function_privilege(%s, %s, 'EXECUTE')",
                        (role, f"public.{function}"),
                    ).fetchone()[0]
                    assert not allowed, f"{role} can call {function}"
                assert not conn.execute(
                    "select has_schema_privilege(%s, 'finledger_private', 'USAGE')", (role,)
                ).fetchone()[0], f"{role} can enter the private auth schema"
                assert not conn.execute(
                    "select has_function_privilege(%s, 'finledger_private.auth_user_by_subject(uuid)', 'EXECUTE')",
                    (role,),
                ).fetchone()[0], f"{role} can call the private auth lookup"

            for table in ("rate_limits", "schema_migrations"):
                enabled = conn.execute(
                    "select relrowsecurity from pg_class where oid = %s::regclass",
                    (f"public.{table}",),
                ).fetchone()[0]
                assert enabled, f"RLS is disabled on {table}"

            for function in ("app_client_id", "app_firm_id"):
                settings = conn.execute(
                    "select proconfig from pg_proc where oid = %s::regprocedure",
                    (f"public.{function}()",),
                ).fetchone()[0]
                assert settings and "search_path=pg_catalog" in settings

            vector_schema = conn.execute(
                "select n.nspname from pg_extension e join pg_namespace n on n.oid=e.extnamespace "
                "where e.extname='vector'"
            ).fetchone()
            if vector_schema:
                assert vector_schema[0] == "extensions"

            conn.execute("create table public.future_private_record (id integer)")
            conn.execute("create function public.future_private_function() returns integer "
                         "language sql as $$ select 1 $$")
            for role in ("anon", "authenticated", "service_role"):
                assert not conn.execute(
                    "select has_table_privilege(%s, 'public.future_private_record', 'SELECT')", (role,)
                ).fetchone()[0], f"{role} can read a future table"
                assert not conn.execute(
                    "select has_function_privilege(%s, 'public.future_private_function()', 'EXECUTE')", (role,)
                ).fetchone()[0], f"{role} can call a future function"
    finally:
        with psycopg.connect(owner_dsn, autocommit=True) as admin:
            if database_created:
                admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(database)))
            for role in reversed(created_roles):
                admin.execute(sql.SQL("drop role {}").format(sql.Identifier(role)))
