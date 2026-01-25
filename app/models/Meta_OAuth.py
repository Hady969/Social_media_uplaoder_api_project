# app/Meta_OAuth.py

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import uuid
import os
import requests
from datetime import datetime, timedelta
import time
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Database connection parameters (same as you were using)
DB_HOST = "localhost"
DB_NAME = "fastapi socialmedia"
DB_USER = "postgres"
DB_PASSWORD = "hady"

META_APP_ID = os.getenv("META_APP_ID_0")
META_APP_SECRET = os.getenv("META_APP_SECRET_0")
META_REDIRECT_URI = os.getenv("META_REDIRECT_URI")

# ----------------- DATABASE HELPER -----------------
def get_db_connection():
    """
    Returns a connection to PostgreSQL.
    Keeps trying if connection fails.
    """
    while True:
        try:
            conn = psycopg2.connect(
                host=DB_HOST,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                cursor_factory=RealDictCursor
            )
            print("Database connected successfully")
            return conn
        except Exception as e:
            print("Database connection failed:", e)
            time.sleep(2)


# ----------------- Pydantic Models -----------------
class ClientCreate(BaseModel):
    name: str
    email: str

# ----------------- ROUTES -----------------
@app.post("/register")
def register_client(client: ClientCreate):
    """
    Register a new client and return the Meta OAuth URL.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    client_id = str(uuid.uuid4())
    
    try:
        cur.execute(
            "INSERT INTO clients (id, name, email) VALUES (%s, %s, %s) RETURNING id;",
            (client_id, client.name, client.email)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        cur.close()
        conn.close()

    oauth_url = (
        f"https://www.facebook.com/v17.0/dialog/oauth?"
        f"client_id={META_APP_ID}&redirect_uri={META_REDIRECT_URI}"
        f"&scope=instagram_basic,pages_show_list,pages_manage_ads,ads_management"
        f"&state={client_id}"
    )

    return {"client_id": client_id, "oauth_url": oauth_url}


@app.get("/auth/meta/callback")
def meta_callback(request: Request, code: str, state: str):
    """
    Exchange Meta authorization code for short-lived token
    and store it in the database.
    """
    token_url = (
        f"https://graph.facebook.com/v17.0/oauth/access_token?"
        f"client_id={META_APP_ID}&redirect_uri={META_REDIRECT_URI}"
        f"&client_secret={META_APP_SECRET}&code={code}"
    )
    resp = requests.get(token_url)
    data = resp.json()

    short_lived_token = data.get("access_token")
    if not short_lived_token:
        raise HTTPException(status_code=400, detail=f"Could not obtain short-lived token: {data}")

    short_lived_expires_at = datetime.utcnow() + timedelta(hours=2)

    # Use the same connection function
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO meta_tokens (client_id, short_lived_token, short_lived_expires_at,
                                     long_lived_token, long_lived_expires_at)
            VALUES (%s, %s, %s, %s, %s);
            """,
            (state, short_lived_token, short_lived_expires_at, None, None)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database insert failed: {e}")
    finally:
        cur.close()
        conn.close()

    return {"message": "Meta access granted, short-lived token stored."}
