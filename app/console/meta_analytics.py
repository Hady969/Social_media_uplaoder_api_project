from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

from app.routers.DB_helpers.meta_token_db_reader import MetaTokenDbReader, DbReadError

load_dotenv()


# ======================
# Errors
# ======================

class MetaAPIError(RuntimeError):
    pass


class TokenLoadError(RuntimeError):
    pass


# ======================
# Config
# ======================

@dataclass(frozen=True)
class MetaConfig:
    graph_api_version: str = "v19.0"
    base_url: str = "https://graph.facebook.com"
    timeout_s: int = 30


LEVEL_MENU = {
    1: "account",
    2: "campaign",
    3: "adset",
    4: "ad",
}

ALLOWED_BREAKDOWNS = {
    "age",
    "gender",
    "country",
    "region",
    "dma",
    "publisher_platform",
    "platform_position",
    "device_platform",
    "impression_device",
}


def last_30_days_range() -> tuple[str, str]:
    today = date.today()
    since = today - timedelta(days=30)
    return since.isoformat(), today.isoformat()


# ======================
# Meta API Client
# ======================

class MetaAnalyticsClient:
    def __init__(self, access_token: str, config: Optional[MetaConfig] = None):
        self.access_token = access_token
        self.config = config or MetaConfig()

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}/{self.config.graph_api_version}/{path.lstrip('/')}"

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = dict(params or {})
        params["access_token"] = self.access_token

        resp = requests.get(self._url(path), params=params, timeout=self.config.timeout_s)
        try:
            data = resp.json()
        except Exception:
            raise MetaAPIError(f"Non-JSON response HTTP {resp.status_code}: {resp.text}")

        if resp.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            raise MetaAPIError(
                f"Meta API error HTTP {resp.status_code}: "
                f"{err.get('message', data)} "
                f"(type={err.get('type')}, code={err.get('code')}, subcode={err.get('error_subcode')})"
            )
        return data

    # -------- IG Profile --------

    def get_ig_profile(self, ig_user_id: str) -> Dict[str, Any]:
        fields = ",".join(
            [
                "id",
                "username",
                "name",
                "followers_count",
                "follows_count",
                "media_count",
                "profile_picture_url",
            ]
        )
        data = self._get(ig_user_id, params={"fields": fields})
        return {
            "ig_user_id": data.get("id"),
            "username": data.get("username"),
            "name": data.get("name"),
            "followers": data.get("followers_count"),
            "following": data.get("follows_count"),
            "media_count": data.get("media_count"),
            "profile_picture_url": data.get("profile_picture_url"),
        }

    # -------- Ad Accounts --------

    def list_my_ad_accounts(self) -> List[Dict[str, Any]]:
        data = self._get(
            "me/adaccounts",
            params={"fields": "id,name,account_status,currency", "limit": 200},
        )
        rows = data.get("data", [])
        return rows if isinstance(rows, list) else []

    # -------- Insights --------

    def get_ad_account_insights(
        self,
        ad_account_id: str,
        since: str,
        until: str,
        level: str,
        breakdowns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if not ad_account_id:
            raise MetaAPIError("ad_account_id is required (cannot be empty).")

        if not ad_account_id.startswith("act_"):
            ad_account_id = f"act_{ad_account_id}"

        params: Dict[str, Any] = {
            "fields": "impressions,reach,clicks,spend,ctr,cpc,cpm",
            "level": level,
            # IMPORTANT FIX: Graph expects JSON string
            "time_range": json.dumps({"since": since, "until": until}),
            "limit": 500,
        }
        if breakdowns:
            params["breakdowns"] = ",".join(breakdowns)

        return self._get(f"{ad_account_id}/insights", params=params)


# ======================
# Console UI
# ======================

class MetaAnalyticsConsole:
    """
    Uses DB-stored tokens.
    - IG profile uses PAGE token by default (fallback user token)
    - Ads uses USER token
    """

    def __init__(self, db: MetaTokenDbReader):
        self.db = db
        self.client_id: Optional[str] = None

        self.page_id: Optional[str] = None
        self.meta_user_id: Optional[str] = None
        self.ig_user_id: Optional[str] = None

        self.ig_client: Optional[MetaAnalyticsClient] = None
        self.ads_client: Optional[MetaAnalyticsClient] = None

    # -------- helpers --------

    def _print_json(self, obj: Any) -> None:
        print(json.dumps(obj, indent=2, ensure_ascii=False))

    def _prompt(self, label: str, default: Optional[str] = None) -> str:
        suffix = f" [{default}]" if default else ""
        val = input(f"{label}{suffix}: ").strip()
        return val or (default or "")

    def _load_context_from_db(self, client_id: str) -> None:
        try:
            page = self.db.get_latest_meta_page_for_client(client_id)
            self.page_id = str(page["page_id"])
        except DbReadError:
            self.page_id = None

        try:
            mu = self.db.get_latest_meta_user_for_client(client_id)
            self.meta_user_id = str(mu["meta_user_id"])
        except DbReadError:
            self.meta_user_id = None

        self.ig_user_id = self.db.get_instagram_actor_id_for_client(client_id)

    def _load_page_or_user_token_for_ig(self, client_id: str) -> str:
        self._load_context_from_db(client_id)

        if self.page_id:
            try:
                tok = self.db.get_active_page_token(client_id=client_id, page_id=self.page_id)
                return tok.access_token
            except DbReadError:
                pass

        if self.meta_user_id:
            tok = self.db.get_active_user_token(client_id=client_id, meta_user_id=self.meta_user_id)
            return tok.access_token

        raise TokenLoadError("No page token or user token available for IG calls.")

    def _load_user_token_for_ads(self, client_id: str) -> str:
        self._load_context_from_db(client_id)
        if not self.meta_user_id:
            raise TokenLoadError("No meta_user_id for this client; cannot load user token.")
        tok = self.db.get_active_user_token(client_id=client_id, meta_user_id=self.meta_user_id)
        return tok.access_token

    def _choose_level(self) -> str:
        print("\nChoose insights level:")
        for k, v in LEVEL_MENU.items():
            print(f"{k}) {v}")
        while True:
            raw = input("Select level number [1]: ").strip() or "1"
            if raw.isdigit() and int(raw) in LEVEL_MENU:
                return LEVEL_MENU[int(raw)]
            print("Invalid choice.")

    def _choose_breakdowns(self) -> Optional[List[str]]:
        raw = self._prompt("Breakdowns (comma-separated or empty, '?' to list)", "")
        if raw.strip() == "?":
            print("Allowed breakdowns:")
            print(", ".join(sorted(ALLOWED_BREAKDOWNS)))
            raw = self._prompt("Breakdowns", "")
        vals = [b.strip() for b in raw.split(",") if b.strip()]
        invalid = [b for b in vals if b not in ALLOWED_BREAKDOWNS]
        if invalid:
            print("Invalid breakdown(s):", ", ".join(invalid))
            print("Allowed breakdowns:", ", ".join(sorted(ALLOWED_BREAKDOWNS)))
            return None
        return vals or None

    def _choose_ad_account(self, accounts: List[Dict[str, Any]]) -> str:
        print("\nAvailable Ad Accounts:")
        for i, a in enumerate(accounts, start=1):
            print(
                f"{i}) {a.get('name')} | id={a.get('id')} | currency={a.get('currency')} | status={a.get('account_status')}"
            )
        while True:
            raw = input("Select ad account number: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(accounts):
                return str(accounts[int(raw) - 1]["id"])
            print("Invalid selection. Enter 1, 2, ...")

    # -------- main flow --------

    def configure(self) -> None:
        self.client_id = self._prompt("client_id (UUID in DB)")
        assert self.client_id

        # Load tokens
        ig_token = self._load_page_or_user_token_for_ig(self.client_id)
        ads_token = self._load_user_token_for_ads(self.client_id)

        self.ig_client = MetaAnalyticsClient(ig_token)
        self.ads_client = MetaAnalyticsClient(ads_token)

        # load ids for convenience
        self._load_context_from_db(self.client_id)

        print("\nLoaded context:")
        print("client_id:", self.client_id)
        print("page_id:", self.page_id)
        print("meta_user_id:", self.meta_user_id)
        print("ig_user_id:", self.ig_user_id)

    def run(self) -> None:
        self.configure()
        assert self.ig_client is not None
        assert self.ads_client is not None

        while True:
            print("\n=== Meta Analytics Console ===")
            print("1) IG profile (uses DB ig_user_id if available)")
            print("2) Show ad accounts")
            print("3) Ad insights (last 30 days)")
            print("4) Reload tokens + ids from DB")
            print("5) Exit")

            choice = input("Choose (1-5): ").strip()

            try:
                if choice == "1":
                    ig_user_id = self._prompt("IG user id", self.ig_user_id or "")
                    if not ig_user_id:
                        raise TokenLoadError("No ig_user_id available. Store one in DB or paste it here.")
                    result = self.ig_client.get_ig_profile(ig_user_id)
                    self._print_json(result)

                elif choice == "2":
                    accounts = self.ads_client.list_my_ad_accounts()
                    self._print_json(accounts)

                elif choice == "3":
                    accounts = self.ads_client.list_my_ad_accounts()
                    if not accounts:
                        raise MetaAPIError("No ad accounts returned (permissions or none exist).")

                    ad_account_id = self._choose_ad_account(accounts)
                    level = self._choose_level()
                    breakdowns = self._choose_breakdowns()

                    since, until = last_30_days_range()
                    print(f"Using date range: {since} → {until}")

                    result = self.ads_client.get_ad_account_insights(
                        ad_account_id=ad_account_id,
                        since=since,
                        until=until,
                        level=level,
                        breakdowns=breakdowns,
                    )
                    print("\n--- AD INSIGHTS ---")
                    self._print_json(result)

                elif choice == "4":
                    # reload everything
                    assert self.client_id
                    ig_token = self._load_page_or_user_token_for_ig(self.client_id)
                    ads_token = self._load_user_token_for_ads(self.client_id)
                    self.ig_client = MetaAnalyticsClient(ig_token)
                    self.ads_client = MetaAnalyticsClient(ads_token)
                    self._load_context_from_db(self.client_id)
                    print("Reloaded.")
                    print("page_id:", self.page_id)
                    print("meta_user_id:", self.meta_user_id)
                    print("ig_user_id:", self.ig_user_id)

                elif choice == "5":
                    return

                else:
                    print("Invalid option.")

            except (MetaAPIError, DbReadError, TokenLoadError) as e:
                print(f"\n[Error] {e}")


# ======================
# Entrypoint
# ======================

def main() -> None:
    db = MetaTokenDbReader(
        database_url=os.environ["DATABASE_URL"],
        fernet_key=os.environ["TOKEN_ENCRYPTION_KEY"],
    )
    MetaAnalyticsConsole(db).run()


if __name__ == "__main__":
    main()
