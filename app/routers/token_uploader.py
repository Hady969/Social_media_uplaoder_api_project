# app/routers/token_uploader.py
# Full corrected version that:
# - uses OAuth page selection helpers
# - uses OAuth.fetch_instagram_account_for_page (no duplicate IG fetch here)
# - uses MetaTokenDbWriter for all DB writes (no redundant store_instagram_account)

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from app.routers.OAuth import OAuth, OAuthError
from app.routers.meta_db_writer import MetaTokenDbWriter


class TokenUploadError(Exception):
    pass


@dataclass(frozen=True)
class OAuthTokenUploadResult:
    client_id: str
    meta_user_id: str
    meta_user_name: Optional[str]
    meta_user_email: Optional[str]
    page_id: str
    page_name: Optional[str]
    ig_user_id: Optional[str]
    ig_username: Optional[str]


class MetaOAuthTokenUploader:
    """
    Page-selection-based OAuth persistence.

    Flow:
      (code,state) -> short-lived user token -> long-lived user token -> /me
      -> list pages (/me/accounts) -> user selects page by index
      -> page access token (prefer from /me/accounts; fallback via /{page_id}?fields=access_token)
      -> upsert client + meta_user + meta_page
      -> fetch IG linked to page via OAuth.fetch_instagram_account_for_page(...)
      -> upsert instagram_account + set page_id
      -> store tokens (user + page)
    """

    def __init__(
        self,
        oauth: OAuth,
        db_writer: MetaTokenDbWriter,
        client_name: str,
        user_scopes: Optional[Sequence[str]] = None,
        page_scopes: Optional[Sequence[str]] = None,
        business_config_id: Optional[str] = None,
    ) -> None:
        self.oauth = oauth
        self.db = db_writer
        self.client_name = client_name
        self.user_scopes = list(user_scopes) if user_scopes else None
        self.page_scopes = list(page_scopes) if page_scopes else None
        self.business_config_id = business_config_id

    # --------------------------
    # URL building
    # --------------------------
    def generate_state(self) -> str:
        return self.oauth.generate_state()

    def build_login_url(self, state: str) -> str:
        if self.business_config_id:
            return self.oauth.build_business_auth_url(state=state, config_id=self.business_config_id)

        if not self.user_scopes:
            raise TokenUploadError(
                "Classic OAuth requires user_scopes. Provide user_scopes or set business_config_id."
            )
        return self.oauth.build_auth_url(scopes=list(self.user_scopes), state=state)

    # --------------------------
    # Optional helper for UI
    # --------------------------
    def get_pages_menu(self, long_lived_user_token: str) -> dict[int, dict[str, Any]]:
        """
        Use this in your UI step to display pages (or call oauth.print_pages_menu()).
        """
        return self.oauth.get_pages_dict(long_lived_user_token)

    # --------------------------
    # Main callback handler (with page selection)
    # --------------------------
    def handle_callback_and_persist(
        self,
        query_params: Dict[str, Any],
        expected_state: str,
        page_choice: int,
    ) -> OAuthTokenUploadResult:
        """
        page_choice: 1-based index corresponding to OAuth.get_pages_dict()
        """
        try:
            # 1) extract code
            code = self.oauth.extract_code_from_callback(query_params, expected_state)

            # 2) code -> short-lived
            short_tok = self.oauth.exchange_code_for_short_lived_token(code)

            # 3) short-lived -> long-lived
            long_tok = self.oauth.exchange_short_lived_for_long_lived_token(short_tok.access_token)

            # 4) /me
            me = self.oauth.get_me(long_tok.access_token)
            meta_user_id = str(me["id"])
            meta_user_name = me.get("name")
            meta_user_email = me.get("email")

            # 5) list pages + select one
            pages = self.oauth.get_pages_dict(long_tok.access_token)
            selected = self.oauth.select_page_by_index(pages, page_choice)

            page_id = str(selected["id"])
            page_name = selected.get("name")

            # 6) page access token: prefer from /me/accounts; else derive
            page_token = selected.get("access_token")
            if not page_token:
                page_token = self.oauth.get_page_access_token(page_id, long_tok.access_token)

            # 7) ensure client + upsert user/page
            client_id = self.db.ensure_client(self.client_name)

            self.db.upsert_meta_user(
                client_id=client_id,
                meta_user_id=meta_user_id,
                name=meta_user_name,
                email=meta_user_email,
            )

            self.db.upsert_meta_page(
                client_id=client_id,
                page_id=page_id,
                connected_meta_user_id=meta_user_id,
            )

            # 8) fetch + store IG account linked to selected page (via OAuth class)
            ig_info = self.oauth.fetch_instagram_account_for_page(page_id, page_token)
            ig_user_id = ig_info.get("ig_user_id")
            ig_username = ig_info.get("ig_username")

            if ig_user_id:
                self.db.upsert_instagram_account(
                    client_id=client_id,
                    ig_user_id=str(ig_user_id),
                    username=ig_username,
                )
                self.db.set_instagram_account_page_id(
                    client_id=client_id,
                    ig_user_id=str(ig_user_id),
                    page_id=page_id,
                )

            # 9) store tokens
            self.db.store_user_and_page_tokens(
                client_id=client_id,
                meta_user_id=meta_user_id,
                user_long_lived_token=long_tok.access_token,
                page_id=page_id,
                page_access_token=page_token,
                user_scopes=self.user_scopes,
                user_expires_in=long_tok.expires_in,
                page_scopes=self.page_scopes,
                page_expires_in=None,
            )

            return OAuthTokenUploadResult(
                client_id=client_id,
                meta_user_id=meta_user_id,
                meta_user_name=meta_user_name,
                meta_user_email=meta_user_email,
                page_id=page_id,
                page_name=page_name,
                ig_user_id=str(ig_user_id) if ig_user_id else None,
                ig_username=ig_username,
            )

        except OAuthError as e:
            raise TokenUploadError(str(e)) from e
        except Exception as e:
            raise TokenUploadError(str(e)) from e
