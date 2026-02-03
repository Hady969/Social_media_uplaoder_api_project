# app/routers/DB_helpers/oauth_session_db.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional, Dict
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras


@dataclass
class OAuthSessionRow:
    state: str
    client_key: str
    redirect_uri: str
    consumed_at: Optional[str] = None
    meta_user_id: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


class OAuthSessionDb:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def create_session(self, provider: str, state: str, client_key: str, redirect_uri: str) -> None:
        q = """
        INSERT INTO oauth_sessions (provider, state, client_key, redirect_uri)
        VALUES (%s, %s, %s, %s)
        """
        with psycopg2.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(q, (provider, state, client_key, redirect_uri))

    def get_session_by_state(self, provider: str, state: str) -> OAuthSessionRow:
        q = """
        SELECT state, client_key, redirect_uri, consumed_at, meta_user_id, extra
        FROM oauth_sessions
        WHERE provider=%s AND state=%s
        """
        with psycopg2.connect(self.database_url) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(q, (provider, state))
                row = cur.fetchone()
                if not row:
                    raise ValueError("state not found")
                return OAuthSessionRow(
                    state=row["state"],
                    client_key=row["client_key"],
                    redirect_uri=row["redirect_uri"],
                    consumed_at=row["consumed_at"].isoformat() if row["consumed_at"] else None,
                    meta_user_id=row.get("meta_user_id"),
                    extra=row.get("extra"),
                )

    def consume_session(
        self,
        provider: str,
        state: str,
        meta_user_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        q = """
        UPDATE oauth_sessions
        SET consumed_at = now(),
            meta_user_id = COALESCE(%s, meta_user_id),
            extra = COALESCE(%s::jsonb, extra)
        WHERE provider=%s AND state=%s AND consumed_at IS NULL
        """
        extra_json = json.dumps(extra) if extra is not None else None
        with psycopg2.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(q, (meta_user_id, extra_json, provider, state))
                if cur.rowcount != 1:
                    raise ValueError("state invalid or already consumed")
