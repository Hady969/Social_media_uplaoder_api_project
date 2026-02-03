# app/routers/Oauth_routers/oauth_meta_callback.py
from __future__ import annotations

import os
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from app.console.token_uploader_console import run_with_code
from app.routers.OAuth import OAuth

router = APIRouter(prefix="/oauth/meta", tags=["oauth"])


def _process_code(code: str, state: str | None) -> None:
    try:
        page_id = os.environ.get("PAGE_ID")
        result = run_with_code(
            code=code,
            expected_state=None,
            received_state=state,
            client_slug="default_client",
            page_id=page_id,
        )

        print("\n✅ OAuth token upload: saved to database")
        print("client_id:", result.get("client_id"))
        print("meta_user_id:", result.get("meta_user_id"))
        print("page_id:", result.get("page_id"))
        print("ig_user_id:", result.get("ig_user_id"))
    except Exception as e:
        print("\n❌ OAuth token upload failed:", repr(e))
        raise



@router.get("/callback", response_class=HTMLResponse)
async def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    background_tasks: BackgroundTasks = None,
):
    if error:
        return PlainTextResponse(
            f"Meta OAuth error: {error}. {error_description or ''}",
            status_code=400,
        )

    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code'.")

    background_tasks.add_task(_process_code, code, state)
    return HTMLResponse("<h3>Authorization received.</h3><p>You can close this tab.</p>")


@router.get("/start")
def start_oauth():
    app_id = os.environ["META_APP_ID_0"]
    app_secret = os.environ["META_APP_SECRET_0"]
    redirect_uri = os.environ["META_REDIRECT_URI"]
    graph_version = os.getenv("GRAPH_API_VERSION", "v17.0")
    config_id = os.environ.get("META_LOGIN_CONFIG_ID")

    if not config_id:
        raise HTTPException(status_code=500, detail="Missing META_LOGIN_CONFIG_ID")

    oauth = OAuth(
        app_id=app_id,
        app_secret=app_secret,
        redirect_uri=redirect_uri,
        graph_version=graph_version,
    )

    state = oauth.generate_state()
    login_url = oauth.build_business_auth_url(state=state, config_id=config_id)
    return RedirectResponse(url=login_url, status_code=302)
