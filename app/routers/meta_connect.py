# app/routers/meta_oauth_routes.py
# SINGLE FILE: start + callback + DB-backed OAuth session handling (NO in-memory state)

from __future__ import annotations

import os
import json
from typing import Optional, Dict, Any
from datetime import datetime

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.routers.OAuth import OAuth, OAuthError
from app.routers.DB_helpers.meta_db_writer import MetaTokenDbWriter
from app.routers.DB_helpers.meta_token_db_reader import MetaTokenDbReader
from app.routers.OAuth_Flow.token_uploader import TokenUploadError


# ==========================================================
# CONFIG
# ==========================================================

DATABASE_URL = os.environ["DATABASE_URL"]
FERNET_KEY = os.environ["TOKEN_ENCRYPTION_KEY"]

META_APP_ID = os.environ["META_APP_ID_0"]
META_APP_SECRET = os.environ["META_APP_SECRET_0"]
META_REDIRECT_URI = os.environ["META_REDIRECT_URI"]
META_LOGIN_CONFIG_ID = os.environ["META_LOGIN_CONFIG_ID"]
GRAPH_VERSION = os.getenv("GRAPH_API_VERSION", "v17.0")

router = APIRouter(prefix="/auth/meta", tags=["meta-oauth"])


# ==========================================================
# DB: OAuth sessions (STATE stored in DB)
# ==========================================================

class OAuthSessionDb:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def create_session(self, state: str, client_key: str, redirect_uri: str) -> None:
        q = """
        INSERT INTO oauth_sessions (provider, state, client_key, redirect_uri)
        VALUES ('meta', %s, %s, %s)
        """
        with psycopg2.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(q, (state, client_key, redirect_uri))

    def get_session(self, state: str) -> Dict[str, Any]:
        q = """
        SELECT *
        FROM oauth_sessions
        WHERE provider='meta' AND state=%s
        """
        with psycopg2.connect(self.database_url) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(q, (state,))
                row = cur.fetchone()
                if not row:
                    raise ValueError("Invalid OAuth state")
                return dict(row)

    def consume_session(
        self,
        state: str,
        meta_user_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        q = """
        UPDATE oauth_sessions
        SET consumed_at = now(),
            meta_user_id = COALESCE(%s, meta_user_id),
            extra = COALESCE(%s::jsonb, extra)
        WHERE provider='meta'
          AND state=%s
          AND consumed_at IS NULL
        """
        with psycopg2.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(q, (meta_user_id, json.dumps(extra) if extra else None, state))
                if cur.rowcount != 1:
                    raise ValueError("OAuth state already consumed or invalid")


# ==========================================================
# HELPERS
# ==========================================================

def oauth_client() -> OAuth:
    return OAuth(
        app_id=META_APP_ID,
        app_secret=META_APP_SECRET,
        redirect_uri=META_REDIRECT_URI,
        graph_version=GRAPH_VERSION,
    )


# ==========================================================
# ROUTES
# ==========================================================

@router.get("/start")
def start_meta_login(client_key: Optional[str] = None):
    """
    Step 1:
    - Generate state
    - Store state in DB
    - Redirect user to Meta login
    """
    oauth = oauth_client()
    session_db = OAuthSessionDb(DATABASE_URL)

    client_key = client_key or "default_client"
    state = oauth.generate_state()

    session_db.create_session(
        state=state,
        client_key=client_key,
        redirect_uri=META_REDIRECT_URI,
    )

    login_url = oauth.build_business_auth_url(
        state=state,
        config_id=META_LOGIN_CONFIG_ID,
    )

    return RedirectResponse(login_url)


@router.get("/callback")
def meta_callback(code: str, state: str):
    """
    Step 2:
    - Meta redirects here
    - Validate state from DB
    - Exchange tokens
    - Store everything in DB
    """
    oauth = oauth_client()
    session_db = OAuthSessionDb(DATABASE_URL)
    writer = MetaTokenDbWriter(DATABASE_URL, FERNET_KEY)

    try:
        session = session_db.get_session(state)
        if session.get("consumed_at"):
            raise HTTPException(status_code=400, detail="OAuth state already used")

        # 1) exchange tokens
        short_tok = oauth.exchange_code_for_short_lived_token(code)
        long_tok = oauth.exchange_short_lived_for_long_lived_token(short_tok.access_token)

        # 2) /me
        me = oauth.get_me(long_tok.access_token)
        meta_user_id = str(me["id"])
        meta_user_name = me.get("name")
        meta_user_email = me.get("email")

        # 3) consume state (single-use)
        session_db.consume_session(
            state=state,
            meta_user_id=meta_user_id,
            extra={"step": "user_authenticated"},
        )

        # 4) ensure client
        client_id = writer.ensure_client(session["client_key"])

        # 5) upsert meta user
        writer.upsert_meta_user(
            client_id=client_id,
            meta_user_id=meta_user_id,
            name=meta_user_name,
            email=meta_user_email,
        )

        # 6) choose page automatically (first page)
        pages = oauth.get_pages_dict(long_tok.access_token)
        if not pages:
            raise OAuthError("No Facebook Pages available for this user")

        first_page = list(pages.values())[0]
        page_id = str(first_page["id"])
        page_token = first_page["access_token"]

        writer.upsert_meta_page(
            client_id=client_id,
            page_id=page_id,
            connected_meta_user_id=meta_user_id,
        )

        # 7) IG account (if exists)
        try:
            ig_info = oauth.fetch_instagram_account_for_page(
                page_id=page_id,
                page_access_token=page_token,
            )
            writer.upsert_instagram_account(
                client_id=client_id,
                ig_user_id=str(ig_info["ig_user_id"]),
                page_id=page_id,
                username=ig_info.get("ig_username"),
            )
        except OAuthError:
            pass

        # 8) store tokens
        writer.store_user_and_page_tokens(
            client_id=client_id,
            meta_user_id=meta_user_id,
            user_long_lived_token=long_tok.access_token,
            page_id=page_id,
            page_access_token=page_token,
            user_scopes=None,
            user_expires_in=long_tok.expires_in,
            page_scopes=None,
            page_expires_in=None,
        )

        return {
            "status": "ok",
            "client_id": client_id,
            "meta_user_id": meta_user_id,
            "page_id": page_id,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
