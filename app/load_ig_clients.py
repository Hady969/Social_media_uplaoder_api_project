from dotenv import load_dotenv
import os
from app.instagram_account import InstagramAccount

load_dotenv()

CLIENT_COUNT = int(os.getenv("CLIENT_COUNT"))
accounts = []  # Array of clients

# Initialize accounts and get long-lived tokens
for i in range(CLIENT_COUNT):
    account = InstagramAccount(
        ig_user_id=os.getenv(f"IG_USER_ID_{i}"),

        app_id=os.getenv(f"META_APP_ID_{i}"),
        app_secret=os.getenv(f"META_APP_SECRET_{i}"),
        user_access_token=os.getenv(f"META_USER_ACCESS_TOKEN_{i}")
    )
    token = account.get_long_lived_access_token()
    print(f"Client {i} long-lived token: {token}\n")
    accounts.append(account)

# Example usage
account_1 = accounts[0]

# Print reels
account_1.print_reels("sportsfusion.st", limit=25)



import requests
from PIL import Image
from io import BytesIO

url = "https://i.imgur.com/ExdKOOz.png"
resp = requests.get(url)
img = Image.open(BytesIO(resp.content))

print("Format:", img.format)
print("Size:", img.size)  # (width, height)
print("File size (KB):", len(resp.content)/1024)

