import os
import requests
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()
GRAPH_API_VERSION = "v17.0"
ACCESS_TOKEN = os.getenv("META_USER_LONG_LIVED_ACCESS_TOKEN_0")
GRAPH_API = "https://graph.facebook.com/v19.0"

PAGE_ACCESS_TOKEN: str | None = None

def exchange_user_token_for_page_token(user_access_token: str, page_id: str) -> str:
    r = requests.get(f"{GRAPH_API}/me/accounts", params={"access_token": user_access_token})
    data = r.json()
    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])
    for page in data.get("data", []):
        if page["id"] == page_id:
            return page["access_token"]
    raise HTTPException(status_code=404, detail="Page not found or user is not an admin")

def persist_page_token(page_token: str):
    env_path = ".env"
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()
    found = False
    for i, line in enumerate(lines):
        if line.startswith("PAGE_ACCESS_TOKEN="):
            lines[i] = f"PAGE_ACCESS_TOKEN={page_token}\n"
            found = True
    if not found:
        lines.append(f"PAGE_ACCESS_TOKEN={page_token}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)
