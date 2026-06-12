import os

from google_auth_oauthlib.flow import InstalledAppFlow


CLIENT_ID = os.environ["YOUTUBE_CLIENT_ID"]
CLIENT_SECRET = os.environ["YOUTUBE_CLIENT_SECRET"]

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

client_config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

auth_url, _ = flow.authorization_url(
    access_type="offline",
    prompt="consent",
    include_granted_scopes="true",
)

print("\nOpen this URL in your browser:\n")
print(auth_url)
print("\nAfter approving, copy the final URL from your browser and paste it here.\n")

redirect_response = input("Paste final redirect URL here: ").strip()

flow.fetch_token(authorization_response=redirect_response)

credentials = flow.credentials

print("\nYOUR_REFRESH_TOKEN:\n")
print(credentials.refresh_token)
