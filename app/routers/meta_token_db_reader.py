# app/services/meta_token_db_reader.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Any, Dict
import hashlib

import psycopg
from psycopg.rows import dict_row
from cryptography.fernet import Fernet

from app.routers.meta_token_crypto import MetaTokenCrypto

class DbReadError(Exception):
    pass


@dataclass(frozen=True)
class ActiveToken:
    owner_type: str         # 'user' or 'page'
    owner_id: str           # meta_user_id or page_id
    access_token: str       # decrypted
    scopes: Sequence[str]
    expires_at: Optional[str] = None


class MetaTokenDbReader:
    """
    Reads encrypted tokens from meta_token and decrypts them.

    Expected columns in meta_token (adjust SQL if your names differ):
      - client_id
      - owner_type ('user'|'page')
      - owner_id
      - access_token_ciphertext
      - status ('active'|'revoked')
      - scopes (json/array/text) optional
      - expires_at optional
      - created_at
    """

    def __init__(self, database_url: str, fernet_key: str) -> None:
        self.database_url = database_url
        self.crypto = MetaTokenCrypto(fernet_key)

    # -------- tokens --------

    def get_active_token(self, client_id: str, owner_type: str, owner_id: str) -> ActiveToken:
        row = self._fetchone(
            """
            SELECT owner_type, owner_id, access_token_ciphertext, scopes, expires_at
            FROM meta_token
            WHERE client_id=%s AND owner_type=%s AND owner_id=%s AND status='active'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (client_id, owner_type, owner_id),
        )
        if not row:
            raise DbReadError(f"No active token found for client_id={client_id} owner_type={owner_type} owner_id={owner_id}")

        token = self._decrypt(row["access_token_ciphertext"])
        scopes = row.get("scopes") or []
  
     
        # scopes can be stored as json/array/text; normalize to list[str]
        if isinstance(scopes, str):
            scopes = [s.strip() for s in scopes.replace(",", " ").split() if s.strip()]
        return ActiveToken(
            owner_type=str(row["owner_type"]),
            owner_id=str(row["owner_id"]),
            access_token=token,
            scopes=list(scopes),
            expires_at=str(row.get("expires_at")) if row.get("expires_at") else None,
        )

    def get_active_user_token(self, client_id: str, meta_user_id: str) -> ActiveToken:
        return self.get_active_token(client_id, "user", meta_user_id)

    def get_active_page_token(self, client_id: str, page_id: str) -> ActiveToken:
        return self.get_active_token(client_id, "page", page_id)

    # -------- metadata lookups (optional helpers) --------

    def get_latest_meta_user_for_client(self, client_id: str) -> Dict[str, Any]:
        row = self._fetchone(
            """
            SELECT meta_user_id, name, email
            FROM meta_user
            WHERE client_id=%s
            ORDER BY created_at DESC NULLS LAST
            LIMIT 1
            """,
            (client_id,),
        )
        if not row:
            raise DbReadError(f"No meta_user found for client_id={client_id}")
        return dict(row)

    def get_latest_meta_page_for_client(self, client_id: str) -> Dict[str, Any]:
        row = self._fetchone(
            """
            SELECT page_id, connected_meta_user_id, name, category
            FROM meta_page
            WHERE client_id=%s
            ORDER BY created_at DESC NULLS LAST
            LIMIT 1
            """,
            (client_id,),
        )
        if not row:
            raise DbReadError(f"No meta_page found for client_id={client_id}")
        return dict(row)

    def get_instagram_actor_id_for_client(self, client_id: str) -> str | None:
        row = self._fetchone(
            """
            SELECT ig_user_id
            FROM instagram_account
            WHERE client_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (client_id,),
        )
        return row["ig_user_id"] if row else None

    # -------- internal --------
    def _decrypt(self, ciphertext: str) -> str:
        return self.crypto.decrypt(ciphertext)


    def _fetchone(self, sql: str, params: tuple) -> Optional[dict]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchone()
        except Exception as e:
            raise DbReadError(str(e)) from e
