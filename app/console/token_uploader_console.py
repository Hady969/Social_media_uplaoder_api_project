# scripts/token_uploader_console.py
from __future__ import annotations

from dotenv import load_dotenv
import os
import webbrowser
from urllib.parse import urlparse, parse_qs

from app.routers.OAuth import OAuth, OAuthError
from app.routers.DB_helpers.meta_db_writer import MetaTokenDbWriter
from app.routers.OAuth_Flow.token_uploader import TokenUploadError

load_dotenv()


def parse_callback_input(raw: str) -> dict[str, str]:
    raw = raw.strip()

    if raw.startswith("http://") or raw.startswith("https://"):
        q = parse_qs(urlparse(raw).query)
        out: dict[str, str] = {}
        if "code" in q and q["code"]:
            out["code"] = q["code"][0]
        if "state" in q and q["state"]:
            out["state"] = q["state"][0]
        return out

    return {"code": raw}


def choose_page(oauth: OAuth, long_lived_user_token: str) -> dict[str, str]:
    pages = oauth.get_pages_dict(long_lived_user_token)

    print("\nAvailable Pages:")
    oauth.print_pages_menu(pages)

    while True:
        choice_raw = input("\nSelect page number: ").strip()
        if not choice_raw.isdigit():
            print("Please enter a number.")
            continue

        choice = int(choice_raw)
        try:
            selected = oauth.select_page_by_index(pages, choice)
            return {
                "id": str(selected.get("id")),
                "name": str(selected.get("name") or ""),
                "access_token": str(selected.get("access_token") or ""),
            }
        except OAuthError as e:
            print(f"Invalid selection: {e}")


def _build_oauth_and_db() -> tuple[OAuth, MetaTokenDbWriter]:
    database_url = os.environ["DATABASE_URL"]
    fernet_key = os.environ["TOKEN_ENCRYPTION_KEY"]

    app_id = os.environ["META_APP_ID_0"]
    app_secret = os.environ["META_APP_SECRET_0"]
    graph_version = os.getenv("GRAPH_API_VERSION", "v17.0")
    redirect_uri = os.environ["META_REDIRECT_URI"]

    oauth = OAuth(
        app_id=app_id,
        app_secret=app_secret,
        redirect_uri=redirect_uri,
        graph_version=graph_version,
    )
    db = MetaTokenDbWriter(database_url=database_url, fernet_key=fernet_key)
    return oauth, db


def _select_page_noninteractive(oauth: OAuth, long_lived_user_token: str, page_id: str) -> dict[str, str]:
    """
    Non-interactive selection:
    - Use /me/accounts list to get page name + token if present
    - Fall back to fetching page access token if token missing
    """
    pages = oauth.get_pages_dict(long_lived_user_token)

    matched = None
    # pages can be list/dict depending on your OAuth helper; handle both safely
    if isinstance(pages, list):
        for p in pages:
            if str(p.get("id")) == str(page_id):
                matched = p
                break
    elif isinstance(pages, dict):
        # common: dict keyed by index or page_id; scan values
        for v in pages.values():
            if isinstance(v, dict) and str(v.get("id")) == str(page_id):
                matched = v
                break

    page_name = (matched or {}).get("name") or ""
    page_token = (matched or {}).get("access_token") or ""

    if not page_token:
        page_token = oauth.get_page_access_token(page_id, long_lived_user_token)

    return {"id": str(page_id), "name": str(page_name), "access_token": str(page_token)}


def run_with_code(
    code: str,
    *,
    expected_state: str | None = None,
    received_state: str | None = None,
    client_slug: str = "default_client",
    page_id: str | None = None,
) -> dict[str, str | None]:
    """
    Non-interactive friendly entrypoint for FastAPI integration.
    Returns a small result dict for logging/testing.
    """
    if expected_state is not None and received_state is not None and received_state != expected_state:
        raise TokenUploadError("Invalid OAuth state (state mismatch).")

    if not code:
        raise TokenUploadError("Missing 'code'.")

    oauth, db = _build_oauth_and_db()

    try:
        # 1) exchange code -> short-lived -> long-lived
        short_tok = oauth.exchange_code_for_short_lived_token(code)
        long_tok = oauth.exchange_short_lived_for_long_lived_token(short_tok.access_token)

        # 2) /me
        me = oauth.get_me(long_tok.access_token)
        meta_user_id = str(me["id"])
        meta_user_name = me.get("name")
        meta_user_email = me.get("email")

        # 3) choose page (non-interactive if page_id provided)
        if page_id:
            selected_page = _select_page_noninteractive(oauth, long_tok.access_token, page_id)
        else:
            selected_page = choose_page(oauth, long_tok.access_token)

        selected_page_id = selected_page["id"]
        page_name = selected_page["name"] or None

        # 4) page access token (already ensured)
        page_token = selected_page.get("access_token") or ""
        if not page_token:
            page_token = oauth.get_page_access_token(selected_page_id, long_tok.access_token)

        # 5) IG account info
        ig_user_id: str | None = None
        ig_username: str | None = None
        try:
            ig_info = oauth.fetch_instagram_account_for_page(
                page_id=selected_page_id,
                page_access_token=page_token
            )
            ig_user_id = ig_info.get("ig_user_id")
            ig_username = ig_info.get("ig_username")
        except OAuthError:
            ig_user_id = None
            ig_username = None

        # 6) create client + upsert meta_user/meta_page
        client_id = db.ensure_client(client_slug)

        db.upsert_meta_user(
            client_id=client_id,
            meta_user_id=meta_user_id,
            name=meta_user_name,
            email=meta_user_email,
        )
        db.upsert_meta_page(
            client_id=client_id,
            page_id=selected_page_id,
            connected_meta_user_id=meta_user_id,
        )

        # 7) upsert instagram_account (page_id NOT NULL)
        if ig_user_id:
            db.upsert_instagram_account(
                client_id=client_id,
                ig_user_id=str(ig_user_id),
                page_id=selected_page_id,
                username=ig_username,
            )

        # 8) store tokens
        db.store_user_and_page_tokens(
            client_id=client_id,
            meta_user_id=meta_user_id,
            user_long_lived_token=long_tok.access_token,
            page_id=selected_page_id,
            page_access_token=page_token,
            user_scopes=None,
            user_expires_in=long_tok.expires_in,
            page_scopes=None,
            page_expires_in=None,
        )

        return {
            "client_id": str(client_id),
            "meta_user_id": meta_user_id,
            "page_id": selected_page_id,
            "page_name": page_name,
            "ig_user_id": ig_user_id,
            "ig_username": ig_username,
        }

    except Exception as e:
        raise TokenUploadError(str(e)) from e


def main() -> None:
    app_id = os.environ["META_APP_ID_0"]
    app_secret = os.environ["META_APP_SECRET_0"]
    graph_version = os.getenv("GRAPH_API_VERSION", "v17.0")
    redirect_uri = os.environ["META_REDIRECT_URI"]
    config_id = os.environ.get("META_LOGIN_CONFIG_ID")

    if not config_id:
        raise RuntimeError("Missing META_LOGIN_CONFIG_ID for business login flow.")

    oauth = OAuth(
        app_id=app_id,
        app_secret=app_secret,
        redirect_uri=redirect_uri,
        graph_version=graph_version,
    )

    state = oauth.generate_state()
    login_url = oauth.build_business_auth_url(state=state, config_id=config_id)

    print("\nOpen this URL to login and approve:")
    print(login_url)
    print("\nAfter login, paste the FULL redirect URL (recommended) OR just the 'code'.")
    print("Expected state:", state)

    try:
        webbrowser.open(login_url, new=2)
    except Exception:
        pass

    callback_raw = input("\nPaste redirect URL or code: ").strip()
    parsed = parse_callback_input(callback_raw)

    code = parsed.get("code")
    received_state = parsed.get("state")

    # Optional: use PAGE_ID env to avoid interactive selection even in terminal
    page_id = os.environ.get("PAGE_ID")

    result = run_with_code(
        code=code or "",
        expected_state=state,
        received_state=received_state,
        client_slug="default_client",
        page_id=page_id,
    )

    print("\n✅ Stored successfully")
    for k, v in result.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
