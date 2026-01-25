# app/services/oauth.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence
import secrets
import requests


class OAuthError(Exception):
    pass


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    token_type: Optional[str] = None
    expires_in: Optional[int] = None


class OAuth:
    """
    Meta OAuth helper:
      - generate state
      - build auth URLs:
          * classic (scope-based)
          * business login (config_id-based; no scope in URL)
      - extract code from callback
      - exchange code -> short-lived token
      - exchange short-lived -> long-lived token
      - derive page access token (fields=access_token)
      - fetch /me (meta_user_id)
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        redirect_uri: str,
        graph_version: str = "v17.0",
        session: Optional[requests.Session] = None,
        timeout_s: int = 20,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.redirect_uri = redirect_uri
        self.graph_version = graph_version
        self.http = session or requests.Session()
        self.timeout_s = timeout_s

    def generate_state(self, nbytes: int = 32) -> str:
        return secrets.token_urlsafe(nbytes)

    def build_auth_url(self, scopes: Sequence[str], state: str) -> str:
        """
        Classic Facebook Login (scope-based).
        Note: In classic flow, Meta accepts scopes separated by commas or spaces;
        we use commas to stay compatible with your existing code.
        """
        base = f"https://www.facebook.com/{self.graph_version}/dialog/oauth"
        scope_str = ",".join(scopes)
        return (
            f"{base}"
            f"?client_id={requests.utils.quote(str(self.app_id), safe='')}"
            f"&redirect_uri={requests.utils.quote(self.redirect_uri, safe='')}"
            f"&response_type=code"
            f"&state={requests.utils.quote(state, safe='')}"
            f"&scope={requests.utils.quote(scope_str, safe='')}"
        )

    def build_business_auth_url(self, state: str, config_id: str) -> str:
        """
        Facebook Login for Businesses (Configuration-based).
        IMPORTANT:
          - Do NOT include scope here; the Configuration controls permissions.
          - config_id is required for the business login configuration.
        """
        base = f"https://www.facebook.com/{self.graph_version}/dialog/oauth"
        return (
            f"{base}"
            f"?client_id={requests.utils.quote(str(self.app_id), safe='')}"
            f"&redirect_uri={requests.utils.quote(self.redirect_uri, safe='')}"
            f"&response_type=code"
            f"&state={requests.utils.quote(state, safe='')}"
            f"&config_id={requests.utils.quote(str(config_id), safe='')}"
        )

    def extract_code_from_callback(self, query_params: Dict[str, Any], expected_state: str) -> str:
        if "error" in query_params:
            raise OAuthError(
                f"Meta OAuth error: {query_params.get('error')} - {query_params.get('error_description')}"
            )

        state = query_params.get("state")
        if not state or str(state) != expected_state:
            raise OAuthError("Invalid OAuth state.")

        code = query_params.get("code")
        if not code:
            raise OAuthError("Missing 'code' in callback.")

        return str(code)

    def exchange_code_for_short_lived_token(self, code: str) -> TokenResponse:
        url = f"https://graph.facebook.com/{self.graph_version}/oauth/access_token"
        params = {
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "redirect_uri": self.redirect_uri,
            "code": code,
        }
        data = self._get_json(url, params)
        token = data.get("access_token")
        if not token:
            raise OAuthError(f"Short-lived token missing: {data}")
        return TokenResponse(token, data.get("token_type"), data.get("expires_in"))

    def exchange_short_lived_for_long_lived_token(self, short_lived_user_token: str) -> TokenResponse:
        url = f"https://graph.facebook.com/{self.graph_version}/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "fb_exchange_token": short_lived_user_token,
        }
        data = self._get_json(url, params)
        token = data.get("access_token")
        if not token:
            raise OAuthError(f"Long-lived token missing: {data}")
        return TokenResponse(token, data.get("token_type"), data.get("expires_in"))

    def get_page_access_token(self, page_id: str, long_lived_user_token: str) -> str:
        url = f"https://graph.facebook.com/{self.graph_version}/{page_id}"
        params = {"fields": "access_token", "access_token": long_lived_user_token}
        data = self._get_json(url, params)
        page_token = data.get("access_token")
        if not page_token:
            raise OAuthError(f"Could not retrieve page token: {data}")
        return str(page_token)

    def get_me(self, user_access_token: str) -> Dict[str, Any]:
        url = f"https://graph.facebook.com/{self.graph_version}/me"
        params = {"fields": "id,name,email", "access_token": user_access_token}
        return self._get_json(url, params)

    def _get_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = self.http.get(url, params=params, timeout=self.timeout_s)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise OAuthError(f"HTTP error: {e}") from e
        except ValueError as e:
            raise OAuthError("Non-JSON response from Meta.") from e

        if isinstance(data, dict) and "error" in data:
            raise OAuthError(str(data["error"]))
        return data
        
    
    def get_page_ids(self, long_lived_user_token: str) -> list[dict[str, Any]]:
        """
        Returns the list of Facebook Pages the user can access.

        Graph endpoint:
        GET /me/accounts
        """
        url = f"https://graph.facebook.com/{self.graph_version}/me/accounts"
        params = {"access_token": long_lived_user_token}
        data = self._get_json(url, params)

        pages = data.get("data")
        if not pages or not isinstance(pages, list):
            raise OAuthError(f"No pages returned from /me/accounts: {data}")

        return pages

    
    def get_pages_dict(
        self,
        long_lived_user_token: str,
    ) -> dict[int, dict[str, Any]]:
        """
        Returns all accessible Facebook Pages as a dictionary indexed from 1.

        {
          1: {"id": "...", "name": "...", "access_token": "..."},
          2: {"id": "...", "name": "...", "access_token": "..."},
          ...
        }
        """
        pages = self.get_page_ids(long_lived_user_token)

        if not pages:
            raise OAuthError("No Facebook Pages available for this user.")

        return {i + 1: page for i, page in enumerate(pages)}


    def print_pages_menu(
        self,
        pages: dict[int, dict[str, Any]],
    ) -> None:
        """
        Prints pages in a numbered menu format.

        Example:
          1) Page One
          2) Page Two
        """
        if not pages:
            raise OAuthError("No pages to print.")

        for idx, page in pages.items():
            name = page.get("name") or "Unnamed Page"
            print(f"{idx}) {name}")


    def select_page_by_index(
        self,
        pages: dict[int, dict[str, Any]],
        choice: int,
    ) -> dict[str, Any]:
        """
        Returns the selected page dictionary using the printed index.
        """
        if choice not in pages:
            raise OAuthError(
                f"Invalid selection {choice}. "
                f"Valid options: {list(pages.keys())}"
            )

        return pages[choice]



    def fetch_instagram_account_for_page(
        self,
        page_id: str,
        page_access_token: str,
    ) -> dict[str, Optional[str]]:
        """
        Returns:
          {"ig_user_id": "..."/None, "ig_username": "..."/None}

        Uses:
          GET /{page_id}?fields=instagram_business_account{id,username}
        """
        url = f"https://graph.facebook.com/{self.graph_version}/{page_id}"
        params: Dict[str, Any] = {
            "fields": "instagram_business_account{id,username}",
            "access_token": page_access_token,
        }

        data = self._get_json(url, params)
        iba = (data or {}).get("instagram_business_account") or {}

        return {
            "ig_user_id": iba.get("id"),
            "ig_username": iba.get("username"),
        }
