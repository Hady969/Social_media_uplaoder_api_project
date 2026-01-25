# app/routers/meta_oauth.py

from fastapi import APIRouter, Request, HTTPException

router = APIRouter(tags=["Meta OAuth"])

@router.get("/auth/meta/callback")
def meta_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    # Meta sends errors here if login fails
    if error:
        raise HTTPException(
            status_code=400,
            detail={
                "error": error,
                "description": error_description,
            },
        )

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    # For now, just confirm redirect works
    # Next step: exchange `code` for access token
    return {
        "ok": True,
        "message": "Meta OAuth redirect successful",
        "code": code,
        "state": state,
    }
