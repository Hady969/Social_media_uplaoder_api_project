# scripts/token_uploader_console.py

from __future__ import annotations

from dotenv import load_dotenv
import os
import webbrowser
from urllib.parse import urlparse, parse_qs

# IMPORTANT: import OAuth from the module where you added:
# - get_pages_dict / print_pages_menu / select_page_by_index
# - fetch_instagram_account_for_page
from app.routers.OAuth import OAuth, OAuthError

from app.routers.meta_db_writer import MetaTokenDbWriter
from app.routers.token_uploader import TokenUploadError

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


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    fernet_key = os.environ["TOKEN_ENCRYPTION_KEY"]

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
    db = MetaTokenDbWriter(database_url=database_url, fernet_key=fernet_key)

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

    if "state" in parsed and parsed["state"] != state:
        raise TokenUploadError("Invalid OAuth state (state mismatch).")

    code = parsed.get("code")
    if not code:
        raise TokenUploadError("Missing 'code'.")

    try:
        # 1) exchange code -> short-lived -> long-lived
        short_tok = oauth.exchange_code_for_short_lived_token(code)
        long_tok = oauth.exchange_short_lived_for_long_lived_token(short_tok.access_token)

        # 2) /me
        me = oauth.get_me(long_tok.access_token)
        meta_user_id = str(me["id"])
        meta_user_name = me.get("name")
        meta_user_email = me.get("email")

        # 3) choose page
        selected_page = choose_page(oauth, long_tok.access_token)
        page_id = selected_page["id"]
        page_name = selected_page["name"] or None

        # 4) page access token (prefer from /me/accounts; fallback if missing)
        page_token = selected_page.get("access_token") or ""
        if not page_token:
            page_token = oauth.get_page_access_token(page_id, long_tok.access_token)

        # 5) IG account info via OAuth method
        ig_user_id: str | None = None
        ig_username: str | None = None
        try:
            ig_info = oauth.fetch_instagram_account_for_page(page_id=page_id, page_access_token=page_token)
            ig_user_id = ig_info.get("ig_user_id")
            ig_username = ig_info.get("ig_username")
        except OAuthError:
            ig_user_id = None
            ig_username = None

        # 6) create client + upsert meta_user/meta_page
        client_id = db.ensure_client("default_client")

        db.upsert_meta_user(
            client_id=client_id,
            meta_user_id=meta_user_id,
            name=meta_user_name,
            email=meta_user_email,
        )
        db.upsert_meta_page(
            client_id=client_id,
            page_id=page_id,
            connected_meta_user_id=meta_user_id,
        )

        # 7) upsert instagram_account INCLUDING page_id (page_id is NOT NULL in your DB)
        if ig_user_id:
            db.upsert_instagram_account(
                client_id=client_id,
                ig_user_id=str(ig_user_id),
                page_id=page_id,
                username=ig_username,
            )

        # 8) store tokens
        db.store_user_and_page_tokens(
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

        print("\n✅ Stored successfully")
        print("client_id:", client_id)
        print("meta_user_id:", meta_user_id)
        print("page_id:", page_id)
        print("page_name:", page_name)
        print("ig_user_id:", ig_user_id)
        print("ig_username:", ig_username)

    except Exception as e:
        raise TokenUploadError(str(e)) from e


if __name__ == "__main__":
    main()
