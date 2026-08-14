"""SQLAlchemy table definitions for the users subsystem.

Core-style ``Table`` objects (same approach as the memory store) so schema
creation stays a single ``metadata.create_all`` call in the app lifespan /
lazy bootstrap.
"""

from __future__ import annotations

from sqlalchemy import Column, Date, Integer, MetaData, String, Table

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("email", String(320), unique=True, index=True),  # stored lowercased (CI)
    # Optional login identifier alongside email. Not DB-unique on purpose:
    # multiple accounts may leave it empty (""); uniqueness of non-empty
    # numbers is enforced in create_user.
    Column("phone", String(32), default="", index=True),
    Column("name", String(200), default=""),
    Column("password_hash", String(128)),
    Column("role", String(32), default="user"),
    Column("tier", String(32), default="gratuit"),
    Column("workspace_id", String(64), default=""),
    Column("created_at", String(64)),
    # --- billing (Paddle) ---
    Column("paddle_customer_id", String(64), default=""),
    Column("paddle_subscription_id", String(64), default=""),
    Column("subscription_status", String(32), default="none"),
    Column("subscription_period_end", String(64), default=""),  # ISO, "" = none
    Column("subscription_cancel_at_period_end", Integer, default=0),
)

# create_all never ALTERs: bootstrap applies these idempotently (duplicate
# column errors are swallowed) so existing databases gain the new columns.
USER_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN paddle_customer_id VARCHAR(64) DEFAULT ''",
    "ALTER TABLE users ADD COLUMN paddle_subscription_id VARCHAR(64) DEFAULT ''",
    "ALTER TABLE users ADD COLUMN subscription_status VARCHAR(32) DEFAULT 'none'",
    "ALTER TABLE users ADD COLUMN subscription_period_end VARCHAR(64) DEFAULT ''",
    "ALTER TABLE users ADD COLUMN subscription_cancel_at_period_end INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN phone VARCHAR(32) DEFAULT ''",
    # Key-value store for admin-adjustable settings (e.g. tier budgets).
    "CREATE TABLE IF NOT EXISTS app_settings (key VARCHAR(128) PRIMARY KEY, value TEXT DEFAULT '')",
]

workspaces = Table(
    "workspaces",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(200)),
    Column("owner_id", String(64), index=True),
    Column("created_at", String(64)),
)

# Metering lands in a later phase; the table exists from day one so usage
# rows can be written without a migration.
usage = Table(
    "usage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(64), index=True),
    Column("day", Date),
    Column("tokens_in", Integer, default=0),
    Column("tokens_out", Integer, default=0),
    Column("requests", Integer, default=0),
)

# Key-value store for admin-adjustable settings (e.g. the "tier_budgets"
# overrides consumed by backend.core.catalog). Text values: JSON when
# structured data is needed.
app_settings = Table(
    "app_settings",
    metadata,
    Column("key", String(128), primary_key=True),
    Column("value", String, default=""),
)

TABLES = {"users": users, "workspaces": workspaces, "usage": usage, "app_settings": app_settings}
