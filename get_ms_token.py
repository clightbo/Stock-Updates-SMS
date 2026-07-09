"""One-time Microsoft sign-in for the daily agenda pipeline.

Runs the OAuth device code flow: prints a link and a short code, you sign in
on your phone, and the resulting refresh token is stored as the
MS_REFRESH_TOKEN repository secret (never printed to the log).

Run via the "Microsoft sign-in" GitHub Actions workflow.

Environment variables:
    GH_PAT         GitHub personal access token with repo scope (required,
                   used to store the secret)
    MS_CLIENT_ID   Azure app client id (optional; defaults to Microsoft's
                   public Graph PowerShell client, so no app registration
                   is needed)
"""

import os
import subprocess
import sys
import time

import requests

# Microsoft's first-party public client (Microsoft Graph Command Line Tools).
# Using it means no Azure app registration is required.
DEFAULT_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"

CLIENT_ID = os.environ.get("MS_CLIENT_ID") or DEFAULT_CLIENT_ID
SCOPES = "offline_access User.Read Mail.Read Calendars.ReadWrite"
BASE = "https://login.microsoftonline.com/common/oauth2/v2.0"


def store_secret(name: str, value: str) -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    subprocess.run(
        ["gh", "secret", "set", name, "--repo", repo],
        input=value.encode(),
        env={**os.environ, "GH_TOKEN": os.environ["GH_PAT"]},
        check=True,
        capture_output=True,
    )


def main() -> None:
    if not os.environ.get("GH_PAT"):
        sys.exit(
            "error: the GH_PAT secret is missing. Create a GitHub personal "
            "access token (classic) with the 'repo' scope at "
            "https://github.com/settings/tokens and add it as a repository "
            "secret named GH_PAT, then re-run this workflow."
        )

    device = requests.post(
        f"{BASE}/devicecode",
        data={"client_id": CLIENT_ID, "scope": SCOPES},
        timeout=30,
    ).json()
    if "device_code" not in device:
        sys.exit(f"error: could not start sign-in: {device}")

    print("=" * 60)
    print("  ACTION NEEDED - sign in to Microsoft")
    print()
    print(f"  1. On your phone or computer, open: {device['verification_uri']}")
    print(f"  2. Enter this code: {device['user_code']}")
    print("  3. Sign in with the Microsoft account whose Outlook")
    print("     email/calendar you want summarized.")
    print()
    print(f"  Waiting for you (expires in {device['expires_in'] // 60} minutes)...")
    print("=" * 60, flush=True)

    interval = device.get("interval", 5)
    deadline = time.time() + device["expires_in"]
    while time.time() < deadline:
        time.sleep(interval)
        result = requests.post(
            f"{BASE}/token",
            data={
                "client_id": CLIENT_ID,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device["device_code"],
            },
            timeout=30,
        ).json()
        error = result.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error:
            sys.exit(f"error: sign-in failed: "
                     f"{result.get('error_description', error)[:300]}")

        store_secret("MS_REFRESH_TOKEN", result["refresh_token"])

        me = requests.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {result['access_token']}"},
            timeout=30,
        ).json()
        print()
        print(f"Success! Signed in as: {me.get('displayName') or me.get('userPrincipalName', 'your account')}")
        print("The MS_REFRESH_TOKEN secret has been stored. You can now run "
              "the 'Daily AI agenda' and 'Notion planner sync' workflows.")
        print("Re-run this workflow any time calendar write permissions change.")
        return

    sys.exit("error: sign-in timed out. Re-run this workflow and enter the "
             "code within 15 minutes.")


if __name__ == "__main__":
    main()
