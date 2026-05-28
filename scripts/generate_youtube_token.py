import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow

# Scopes required for uploading videos and managing playlists
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]

def main():
    print("--- YouTube Token Generator ---")
    print("1. Go to Google Cloud Console (https://console.cloud.google.com/)")
    print("2. Create a project and enable 'YouTube Data API v3'")
    print("3. Go to 'Credentials', create an 'OAuth 2.0 Client ID' of type 'Desktop App'")
    print("4. Download the JSON file and save it as 'client_secrets.json' in this folder.")

    if not os.path.exists("client_secrets.json"):
        print("\nError: 'client_secrets.json' not found. Please follow the steps above.")
        return

    flow = InstalledAppFlow.from_client_secrets_file("client_secrets.json", SCOPES)
    creds = flow.run_local_server(port=0)

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }

    print("\n--- YOUR YOUTUBE_TOKEN_JSON ---")
    print("Copy the entire line below and paste it into your GitHub Repository Secret named YOUTUBE_TOKEN_JSON:")
    print("\n" + json.dumps(token_data))
    print("\n-------------------------------")

if __name__ == "__main__":
    main()
