"""SQLAlchemy table definitions for the users subsystem.

Core-style ``Table`` objects (same approach as the memory store) so schema
creation stays a single ``metadata.create_all`` call in the app lifespan /
lazy bootstrap.
"""

from __future__ import annotations

from sqlalchemy import Column, Date, Float, Integer, MetaData, String, Table, Text

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
    # Legacy subscription value retained for backward-compatible rows/tokens;
    # it no longer controls access. Existing billing columns are not dropped.
    Column("tier", String(32), default="gratuit"),
    Column("is_active", Integer, default=1),
    Column("workspace_id", String(64), default=""),
    Column("created_at", String(64)),
)

# create_all never ALTERs: bootstrap applies these idempotently (duplicate
# column errors are swallowed) so existing databases gain the new columns.
USER_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1",
    "ALTER TABLE users ADD COLUMN phone VARCHAR(32) DEFAULT ''",
    # Key-value store for admin-adjustable settings (e.g. the global daily budget).
    "CREATE TABLE IF NOT EXISTS app_settings (key VARCHAR(128) PRIMARY KEY, value TEXT DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS bookmarks ("
    "id VARCHAR(64) PRIMARY KEY, "
    "user_id VARCHAR(64), "
    "query TEXT DEFAULT '', "
    "answer TEXT DEFAULT '', "
    "confidence FLOAT DEFAULT 0, "
    "session_id VARCHAR(128) DEFAULT '', "
    "created_at VARCHAR(64)"
    ")",
    "CREATE TABLE IF NOT EXISTS shared_answers ("
    "token VARCHAR(64) PRIMARY KEY, "
    "user_id VARCHAR(64), "
    "query TEXT DEFAULT '', "
    "answer TEXT DEFAULT '', "
    "citations_json TEXT DEFAULT '', "
    "confidence FLOAT DEFAULT 0, "
    "created_at VARCHAR(64)"
    ")",
    "CREATE TABLE IF NOT EXISTS push_tokens ("
    "token VARCHAR(128) PRIMARY KEY, "
    "user_id VARCHAR(64), "
    "device_id VARCHAR(128) DEFAULT '', "
    "created_at VARCHAR(64)"
    ")",
    "CREATE TABLE IF NOT EXISTS user_prompts ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "user_id VARCHAR(64), "
    "email VARCHAR(320), "
    "prompt TEXT, "
    "source VARCHAR(32), "
    "session_id VARCHAR(128) DEFAULT '', "
    "created_at VARCHAR(64), "
    "metadata TEXT DEFAULT ''"
    ")",
    "CREATE TABLE IF NOT EXISTS user_dossiers ("
    "id VARCHAR(64) PRIMARY KEY, "
    "user_id VARCHAR(64), "
    "name VARCHAR(500) DEFAULT '', "
    "original_name VARCHAR(500) DEFAULT '', "
    "extension VARCHAR(16) DEFAULT '', "
    "size_bytes INTEGER DEFAULT 0, "
    "char_count INTEGER DEFAULT 0, "
    "page_count INTEGER DEFAULT 0, "
    "status VARCHAR(32) DEFAULT 'ready', "
    "error TEXT DEFAULT '', "
    "created_at VARCHAR(64)"
    ")",
    # Case folders ("Mes dossiers" restructure): a folder groups several
    # uploaded documents into one case the AI can analyse as a whole.
    "CREATE TABLE IF NOT EXISTS dossier_folders ("
    "id VARCHAR(64) PRIMARY KEY, "
    "user_id VARCHAR(64), "
    "name VARCHAR(500) DEFAULT '', "
    "description TEXT DEFAULT '', "
    "created_at VARCHAR(64)"
    ")",
    "ALTER TABLE user_dossiers ADD COLUMN folder_id VARCHAR(64) DEFAULT ''",
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

# Key-value store for admin-adjustable settings (e.g. the "daily_budgets"
# override consumed by backend.core.catalog). Text values: JSON when
# structured data is needed.
app_settings = Table(
    "app_settings",
    metadata,
    Column("key", String(128), primary_key=True),
    Column("value", String, default=""),
)

# Audit trail of every user prompt (search + chat). Kept indefinitely by
# default; a retention job can be added later if the table grows.
user_prompts = Table(
    "user_prompts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(64), index=True),
    Column("email", String(320)),
    Column("prompt", Text),
    Column("source", String(32), index=True),  # search, chat, chat_stream, ws_chat
    Column("session_id", String(128), default=""),
    Column("created_at", String(64)),
    Column("metadata", Text, default=""),  # JSON-encoded extra fields
)

# Saved answers (user bookmarks) — the answer snapshot survives history deletion.
bookmarks = Table(
    "bookmarks",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(64), index=True),
    Column("query", Text, default=""),
    Column("answer", Text, default=""),
    Column("confidence", Float, default=0),
    Column("session_id", String(128), default=""),
    Column("created_at", String(64)),
)

# Public read-only snapshots behind share tokens (no auth on read).
shared_answers = Table(
    "shared_answers",
    metadata,
    Column("token", String(64), primary_key=True),
    Column("user_id", String(64), index=True),
    Column("query", Text, default=""),
    Column("answer", Text, default=""),
    Column("citations_json", Text, default=""),  # JSON-encoded citation list
    Column("confidence", Float, default=0),
    Column("created_at", String(64)),
)

# Personal documents ("Mes dossiers"): user-uploaded files for AI analysis.
# The extracted text and the original file live on disk under
# data_dir/user_dossiers/<user_id>/<dossier_id>/; this table is the index.
# folder_id groups documents into a case folder ("" = unfiled).
user_dossiers = Table(
    "user_dossiers",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(64), index=True),
    Column("folder_id", String(64), default="", index=True),
    Column("name", String(500), default=""),
    Column("original_name", String(500), default=""),
    Column("extension", String(16), default=""),
    Column("size_bytes", Integer, default=0),
    Column("char_count", Integer, default=0),
    Column("page_count", Integer, default=0),
    Column("status", String(32), default="ready"),  # ready | error
    Column("error", Text, default=""),
    Column("created_at", String(64)),
)

# Case folders ("Mes dossiers"): a named folder groups several uploaded
# documents into one case; the AI can answer questions or produce a full case
# analysis across every document in the folder plus the legal corpus.
dossier_folders = Table(
    "dossier_folders",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(64), index=True),
    Column("name", String(500), default=""),
    Column("description", Text, default=""),
    Column("created_at", String(64)),
)

# Expo push tokens (ExponentPushToken[...]) for freshness notifications.
push_tokens = Table(
    "push_tokens",
    metadata,
    Column("token", String(128), primary_key=True),
    Column("user_id", String(64), index=True),
    Column("device_id", String(128), default=""),
    Column("created_at", String(64)),
)

TABLES = {
    "users": users,
    "workspaces": workspaces,
    "usage": usage,
    "app_settings": app_settings,
    "user_prompts": user_prompts,
    "bookmarks": bookmarks,
    "shared_answers": shared_answers,
    "push_tokens": push_tokens,
    "user_dossiers": user_dossiers,
    "dossier_folders": dossier_folders,
}
