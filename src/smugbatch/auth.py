"""OAuth 1.0a flow for SmugMug API."""

import webbrowser

import click
import requests
from requests_oauthlib import OAuth1Session

from .config import load_config, save_config

REQUEST_TOKEN_URL = "https://api.smugmug.com/services/oauth/1.0a/getRequestToken"
AUTHORIZE_URL = "https://api.smugmug.com/services/oauth/1.0a/authorize"
ACCESS_TOKEN_URL = "https://api.smugmug.com/services/oauth/1.0a/getAccessToken"
AUTH_USER_URL = "https://api.smugmug.com/api/v2!authuser"


def run_oauth_flow():
    """Perform the full OAuth 1.0a PIN-based flow."""
    config = load_config()
    consumer_key = config["oauth"]["consumer_key"]
    consumer_secret = config["oauth"]["consumer_secret"]

    # Step 1: Get request token
    oauth = OAuth1Session(consumer_key, client_secret=consumer_secret, callback_uri="oob")
    resp = oauth.fetch_request_token(REQUEST_TOKEN_URL)
    request_token = resp["oauth_token"]
    request_token_secret = resp["oauth_token_secret"]

    # Step 2: Direct user to authorize
    auth_url = f"{AUTHORIZE_URL}?oauth_token={request_token}&Access=Full&Permissions=Modify"
    click.echo(f"Opening browser to authorize...\n{auth_url}")
    webbrowser.open(auth_url)

    # Step 3: Get PIN from user
    pin = click.prompt("\nEnter the 6-digit PIN from SmugMug")

    # Step 4: Exchange for access token
    oauth = OAuth1Session(
        consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=request_token,
        resource_owner_secret=request_token_secret,
        verifier=pin,
    )
    resp = oauth.fetch_access_token(ACCESS_TOKEN_URL)

    config["oauth"]["access_token"] = resp["oauth_token"]
    config["oauth"]["access_token_secret"] = resp["oauth_token_secret"]
    save_config(config)
    click.echo("Access token saved to config.")


def check_auth():
    """Validate OAuth tokens and session cookie."""
    config = load_config()

    # Check OAuth
    oauth_ok = False
    consumer_key = config["oauth"]["consumer_key"]
    consumer_secret = config["oauth"]["consumer_secret"]
    access_token = config["oauth"].get("access_token", "")
    access_token_secret = config["oauth"].get("access_token_secret", "")

    if not access_token or not access_token_secret:
        click.echo("OAuth: NOT CONFIGURED (run `smugbatch auth` first)")
    else:
        oauth = OAuth1Session(
            consumer_key,
            client_secret=consumer_secret,
            resource_owner_key=access_token,
            resource_owner_secret=access_token_secret,
        )
        resp = oauth.get(AUTH_USER_URL, headers={"Accept": "application/json"})
        if resp.status_code == 200:
            data = resp.json()
            nickname = data["Response"]["User"]["NickName"]
            click.echo(f"OAuth: OK (logged in as {nickname})")
            oauth_ok = True
        else:
            click.echo(f"OAuth: FAILED (status {resp.status_code})")

    # Check SMSESS cookie
    smsess = config.get("session", {}).get("smsess", "")
    if not smsess:
        click.echo("Session cookie: NOT CONFIGURED (set smsess in config)")
    else:
        resp = requests.get(
            "https://www.smugmug.com/api/v2!authuser",
            cookies={"SMSESS": smsess},
            headers={"Accept": "application/json"},
            allow_redirects=False,
        )
        if resp.status_code == 200:
            click.echo("Session cookie: OK")
        else:
            click.echo(f"Session cookie: FAILED (status {resp.status_code})")

    return oauth_ok
