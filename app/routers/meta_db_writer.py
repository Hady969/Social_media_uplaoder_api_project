# app/services/meta_db_writer.py

from __future__ import annotations
from typing import Any, Mapping
import psycopg
from psycopg import sql

from dataclasses import dataclass
from typing import Optional, Sequence
import hashlib
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from cryptography.fernet import Fernet
from app.routers.meta_token_crypto import MetaTokenCrypto


class DbWriteError(Exception):
    pass


@dataclass(frozen=True)
class StoredToken:
    owner_type: str         # 'user' or 'page'
    owner_id: str           # meta_user_id or page_id
    access_token: str
    scopes: Optional[Sequence[str]] = None
    expires_in: Optional[int] = None  # seconds


class MetaTokenDbWriter:
    """
    Writes:
      - client
      - meta_user
      - meta_page
      - meta_token (encrypted; enforces one active token rule by flipping existing active to revoked)

    Expects your schema:
      client(client_id uuid pk, name, created_at)
      meta_user(client_id, meta_user_id, name, email, created_at) pk(client_id, meta_user_id)
      meta_page(client_id, page_id, connected_meta_user_id, name, category, created_at) pk(client_id, page_id)
      meta_token(token_id uuid pk, client_id, owner_type, owner_id, access_token_ciphertext, token_fingerprint, scopes, expires_at, status, last_validated_at, created_at, updated_at)
      plus partial unique index: one active token per (client_id, owner_type, owner_id)
    """

    def __init__(self, database_url: str, fernet_key: str) -> None:
        self.database_url = database_url
        self.crypto = MetaTokenCrypto(fernet_key)

    # ---------- public helpers ----------

    def ensure_client(self, name: str) -> str:
        """
        Returns client_id as string UUID.
        Creates if not exists (by name).
        """
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT client_id FROM client WHERE name=%s", (name,))
                row = cur.fetchone()
                if row:
                    return str(row["client_id"])

                cur.execute(
                    "INSERT INTO client (name) VALUES (%s) RETURNING client_id",
                    (name,),
                )
                client_id = cur.fetchone()["client_id"]
                conn.commit()
                return str(client_id)

    def upsert_meta_user(self, client_id: str, meta_user_id: str, name: Optional[str], email: Optional[str]) -> None:
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO meta_user (client_id, meta_user_id, name, email)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (client_id, meta_user_id)
                    DO UPDATE SET name = EXCLUDED.name, email = EXCLUDED.email
                    """,
                    (client_id, meta_user_id, name, email),
                )
                conn.commit()

    def upsert_meta_page(
        self,
        client_id: str,
        page_id: str,
        connected_meta_user_id: str,
        name: Optional[str] = None,
        category: Optional[str] = None,
    ) -> None:
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO meta_page (client_id, page_id, connected_meta_user_id, name, category)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (client_id, page_id)
                    DO UPDATE SET
                      connected_meta_user_id = EXCLUDED.connected_meta_user_id,
                      name = COALESCE(EXCLUDED.name, meta_page.name),
                      category = COALESCE(EXCLUDED.category, meta_page.category)
                    """,
                    (client_id, page_id, connected_meta_user_id, name, category),
                )
                conn.commit()

    from datetime import datetime, timezone, timedelta

    def store_token(self, client_id: str, token: StoredToken) -> None:
        """
        Stores token encrypted. Enforces 'one active token per owner' by revoking existing active token first.
        Forces expires_at to 58 days from now (UTC), regardless of token.expires_in.
        """
        now = datetime.now(timezone.utc)

        # Force expiry to 58 days from now
        expires_at = now + timedelta(days=58)

        ciphertext = self._encrypt(token.access_token)
        fingerprint = self._fingerprint(token.access_token)
        scopes = list(token.scopes) if token.scopes else []

        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                # revoke any currently active token for this owner
                cur.execute(
                    """
                    UPDATE meta_token
                    SET status='revoked', updated_at=now()
                    WHERE client_id=%s AND owner_type=%s AND owner_id=%s AND status='active'
                    """,
                    (client_id, token.owner_type, token.owner_id),
                )

                # insert new active token
                cur.execute(
                    """
                    INSERT INTO meta_token (
                    client_id, owner_type, owner_id,
                    access_token_ciphertext, token_fingerprint,
                    scopes, expires_at, status,
                    last_validated_at, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, now(), now())
                    """,
                    (
                        client_id,
                        token.owner_type,
                        token.owner_id,
                        ciphertext,
                        fingerprint,
                        scopes,
                        expires_at,
                        now,  # last_validated_at
                    ),
                )

                conn.commit()

    # ---------- convenience: store both user + page tokens ----------
    def store_user_and_page_tokens(
        self,
        client_id: str,
        meta_user_id: str,
        user_long_lived_token: str,
        page_id: str,
        page_access_token: str,
        user_scopes: Optional[Sequence[str]] = None,
        user_expires_in: Optional[int] = None,
        page_scopes: Optional[Sequence[str]] = None,
        page_expires_in: Optional[int] = None,
    ) -> None:
        self.store_token(
            client_id,
            StoredToken(
                owner_type="user",
                owner_id=meta_user_id,
                access_token=user_long_lived_token,
                scopes=user_scopes,
                expires_in=user_expires_in,
            ),
        )
        self.store_token(
            client_id,
            StoredToken(
                owner_type="page",
                owner_id=page_id,
                access_token=page_access_token,
                scopes=page_scopes,
                expires_in=page_expires_in,
            ),
        )

    def set_ig_user_id(
    self,
    client_id: str,
    ig_user_id: str,
) -> None:
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE instagram_account
                    SET ig_user_id = %s
                    WHERE client_id = %s
                    """,
                    (ig_user_id, client_id),
                )
                conn.commit()


# --- Instagram account writers (for table: instagram_account) ---
    def upsert_instagram_account(
        self,
        client_id: str,
        ig_user_id: str,
        page_id: str,
        username: Optional[str] = None,
    ) -> None:
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO instagram_account (client_id, ig_user_id, username, page_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (client_id, ig_user_id)
                    DO UPDATE SET
                    username = COALESCE(EXCLUDED.username, instagram_account.username),
                    page_id = EXCLUDED.page_id
                    """,
                    (client_id, ig_user_id, username, page_id),
                )
                conn.commit()


    def set_instagram_account_page_id(
            self,
            client_id: str,
            ig_user_id: str,
            page_id: str,
        ) -> None:
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE instagram_account
                        SET page_id = %s
                        WHERE client_id = %s
                        AND ig_user_id = %s
                        """,
                        (page_id, client_id, ig_user_id),
                    )
                    conn.commit()

    def upsert_ad_account(self, data: Mapping[str, Any]) -> None:
        """
        UPSERT into ad_account.
        Requires a UNIQUE constraint matching the conflict target below.
        Adjust conflict columns to match your schema.
        """
        conflict_cols = ["client_id", "meta_ad_account_id"]  # <-- adjust if needed
        self._upsert_simple("ad_account", data, conflict_cols)

    def upsert_campaign(self, data: Mapping[str, Any]) -> None:
        """
        UPSERT into campaign.
        Adjust conflict columns to match your schema.
        """
        conflict_cols = ["client_id", "meta_campaign_id"]  # <-- adjust if needed
        self._upsert_simple("campaign", data, conflict_cols)

    def upsert_ad_set(self, data: Mapping[str, Any]) -> None:
        """
        UPSERT into ad_set.
        Adjust conflict columns to match your schema.
        """
        conflict_cols = ["client_id", "meta_ad_set_id"]  # <-- adjust if needed
        self._upsert_simple("ad_set", data, conflict_cols)

    def upsert_ad(self, data: Mapping[str, Any]) -> None:
        """
        UPSERT into ad.
        Adjust conflict columns to match your schema.
        """
        conflict_cols = ["client_id", "meta_ad_id"]  # <-- adjust if needed
        self._upsert_simple("ad", data, conflict_cols)

    # ---- private helper (not part of your “4” public functions) ----
    def _upsert_simple(self, table: str, data: Mapping[str, Any], conflict_cols: list[str]) -> None:
        if not data:
            raise ValueError("data is empty")
        if not conflict_cols:
            raise ValueError("conflict_cols is empty")

        cols = list(data.keys())
        vals = [data[c] for c in cols]

        # update all columns except conflict columns
        update_cols = [c for c in cols if c not in conflict_cols]
        if not update_cols:
            raise ValueError("No updatable columns (data only contains conflict columns).")

        q = sql.SQL("""
            INSERT INTO {t} ({cols})
            VALUES ({placeholders})
            ON CONFLICT ({conflict})
            DO UPDATE SET {set_clause}
        """).format(
            t=sql.Identifier(table),
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
            placeholders=sql.SQL(", ").join(sql.Placeholder() for _ in cols),
            conflict=sql.SQL(", ").join(map(sql.Identifier, conflict_cols)),
            set_clause=sql.SQL(", ").join(
                sql.SQL("{c}=EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in update_cols
            ),
        )

        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(q, vals)
            conn.commit()
  

    def _encrypt(self, plaintext: str) -> str:
       return self.crypto.encrypt(plaintext)

    def _fingerprint(self, plaintext: str) -> str:
      return self.crypto.fingerprint(plaintext)
