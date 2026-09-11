#!/usr/bin/env python3
"""Upload videos to YouTube from the command line."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SHARED_PROJECT_HINT = "gen-lang-client"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

CATEGORY_DEFAULTS = {
    "22": "People & Blogs",
    "24": "Entertainment",
    "28": "Science & Technology",
    "1": "Film & Animation",
    "20": "Gaming",
    "27": "Education",
    "10": "Music",
}

PRIVACY_CHOICES = ["public", "unlisted", "private"]


def parse_publish_at(value):
    raw = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M")
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"Invalid publish time: {value!r}. "
                "Use 'YYYY-MM-DD HH:MM' or an ISO 8601 timestamp."
            )
    if dt.tzinfo is None:
        dt = dt.astimezone()
    dt = dt.astimezone(timezone.utc)
    if dt <= datetime.now(timezone.utc):
        raise argparse.ArgumentTypeError(
            f"Publish time must be in the future: {value}"
        )
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def auth_blocked_help(client_secret_path):
    try:
        with open(client_secret_path) as f:
            project_id = json.load(f).get("installed", {}).get("project_id", "")
    except Exception:
        project_id = ""
    print("\nGoogle blocked this app's login (unverified OAuth client).")
    print("This is a Google Cloud setting, not a script bug.\n")
    if project_id:
        print(f"Project ID: {project_id}")
    if SHARED_PROJECT_HINT in project_id:
        print(
            "WARNING: this client was auto-generated ('gen-lang-client-*'), which is "
            "typical of API keys leaked/shared online. If you did not create this "
            "project yourself, make your own credential — you cannot unblock someone "
            "else's app.\n"
        )
        print("Fix it yourself:")
        print(
            "  1. Create an OAuth client: https://console.cloud.google.com/apis/credentials"
        )
        print(
            "  2. Configure the consent screen: https://console.cloud.google.com/apis/credentials/consent"
        )
        print(
            "  3. Set app name, support email, and add your Google account as a Test user"
        )
        print(
            "  4. Download the client_secret.json and pass it with --client-secret"
        )
    else:
        print("Since this project is yours:")
        print(
            "  1. Google Cloud Console -> APIs & Services -> OAuth consent screen"
        )
        print("     (https://console.cloud.google.com/apis/credentials/consent)")
        print("  2. Add your Google account email to 'Test users' and save")
        print("  3. Re-run this script; the consent screen should then work\n")
        print(
            "Alternative if still blocked: click 'Advanced' -> 'Go to <app> (unsafe)' "
            "once in the consent screen, then the flow continues."
        )
    sys.exit(1)


def get_credentials(client_secret_path, token_path):
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    client_secret_path, SCOPES
                )
                creds = flow.run_local_server(port=0, prompt="consent")
            except Exception as exc:
                detail = str(exc)
                if (
                    "access_denied" in detail
                    or "unverified" in detail
                    or "blocked" in detail
                ):
                    auth_blocked_help(client_secret_path)
                raise
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return creds


def pick_category(api, video_path):
    try:
        resp = api.videoCategories().list(part="snippet", regionCode="US").execute()
        cats = sorted(
            resp.get("items", []),
            key=lambda c: c["snippet"]["title"].lower(),
        )
    except HttpError:
        cats = []
    options = []
    for c in cats:
        title = c["snippet"]["title"]
        cid = c["id"]
        if title == "Music" or title == "Entertainment" or title == "Gaming":
            options.insert(0, f"{cid}: {title}")
        else:
            options.append(f"{cid}: {title}")
    print("Available categories:")
    for opt in options:
        print(f"  {opt}")
    while True:
        choice = input("Category ID: ").strip()
        if choice in {c["id"] for c in cats}:
            return choice
        print("Invalid category ID.")


def publish_video(
    api,
    video_path,
    title,
    description=None,
    tags=None,
    category="22",
    privacy="private",
    kids=False,
    publish_at=None,
    chunk_size=8 * 1024 * 1024,
    progress_cb=None,
):
    privacy = "private" if publish_at else privacy
    status = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": kids,
    }
    if publish_at:
        status["publishAt"] = publish_at
    body = {
        "snippet": {
            "title": title,
            "description": description or "",
            "tags": tags or [],
            "categoryId": category,
        },
        "status": status,
    }
    media = MediaFileUpload(
        video_path,
        chunksize=chunk_size,
        resumable=True,
    )
    request = api.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )
    response = None
    prev = -1
    while response is None:
        status, response = request.next_chunk()
        if status and status.progress() != prev:
            prev = status.progress()
            pct = int(prev * 100)
            if progress_cb:
                progress_cb(pct)
            else:
                print(f"\rUploading... {pct}%", end="", flush=True)
    if progress_cb:
        progress_cb(100)
    else:
        print(f"\rUploading... 100%   ")
    return response


def upload_video(api, args):
    return publish_video(
        api,
        args.video,
        args.title,
        description=args.description,
        tags=args.tags,
        category=args.category,
        privacy=args.privacy,
        kids=args.kids,
        publish_at=args.publish_at,
        chunk_size=args.chunk,
    )


def set_thumbnail(api, video_id, thumbnail_path):
    api.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(thumbnail_path),
    ).execute()
    print(f"Thumbnail set for {video_id}")


def add_to_playlist(api, video_id, playlist_id):
    api.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()
    print(f"Added to playlist {playlist_id}")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Upload videos to YouTube.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("video", help="Path to the video file to upload")
    parser.add_argument("title", help="Video title (up to 100 chars)")
    parser.add_argument("-d", "--description", help="Video description")
    parser.add_argument(
        "-t", "--tags", nargs="*", help="Comma-free tags (space separated)"
    )
    parser.add_argument(
        "-c", "--category", default="22", help="Numeric YouTube category ID"
    )
    parser.add_argument(
        "-p",
        "--privacy",
        default="private",
        choices=PRIVACY_CHOICES,
        help="Privacy status (ignored when --publish-at is used; scheduled videos must be private)",
    )
    parser.add_argument(
        "--publish-at",
        type=parse_publish_at,
        metavar="'YYYY-MM-DD HH:MM'",
        help="Auto-publish the video at this future time",
    )
    parser.add_argument(
        "-k",
        "--kids",
        dest="kids",
        action="store_true",
        default=False,
        help="Mark video as made for kids",
    )
    parser.add_argument("--thumbnail", help="Path to thumbnail image (recommended 1280x720)")
    parser.add_argument("--playlist", help="Playlist ID to add the video to")
    parser.add_argument(
        "--chunk",
        type=int,
        default=8 * 1024 * 1024,
        help="Upload chunk size in bytes",
    )
    parser.add_argument(
        "--no-confirm", action="store_true", help="Skip the confirmation prompt"
    )
    parser.add_argument(
        "--client-secret",
        default=os.path.join(here, "client_secret.json"),
        help="Path to OAuth client secret JSON",
    )
    parser.add_argument(
        "--token",
        default=os.path.join(here, "token.json"),
        help="Path to stored OAuth token",
    )
    parser.add_argument(
        "--pick-category",
        action="store_true",
        help="Choose category from an interactive list",
    )
    args = parser.parse_args()

    if not os.path.exists(args.video):
        sys.exit(f"Video not found: {args.video}")
    if not os.path.exists(args.client_secret):
        sys.exit(
            f"client_secret.json not found at {args.client_secret}. "
            "Download it from Google Cloud Console -> Credentials -> your OAuth client -> Download JSON"
        )
    if len(args.title) > 100:
        sys.exit("Title must be 100 characters or fewer")

    creds = get_credentials(args.client_secret, args.token)
    api = build("youtube", "v3", credentials=creds)

    if args.category == "pick" or (args.pick_category and not args.category):
        args.category = pick_category(api, args.video)

    print("\n--- Upload details ---")
    print(f"Video:    {args.video}")
    print(f"Title:    {args.title}")
    print(f"Privacy:  {'private (scheduled)' if args.publish_at else args.privacy}")
    if args.publish_at:
        print(f"Scheduled for: {args.publish_at}")
    print(f"Category: {CATEGORY_DEFAULTS.get(args.category, args.category)}")
    print(f"Made for kids: {args.kids}")
    print("---------------------")
    if not args.no_confirm and input("Proceed? [y/N]: ").lower() != "y":
        sys.exit("Cancelled.")

    try:
        resp = upload_video(api, args)
        video_id = resp["id"]
        print(f"Uploaded: https://youtu.be/{video_id}")
        if args.thumbnail:
            set_thumbnail(api, video_id, args.thumbnail)
        if args.playlist:
            add_to_playlist(api, video_id, args.playlist)
    except HttpError as e:
        detail = e.error_details or []
        reasons = [d.get("reason") for d in detail if isinstance(d, dict)]
        sys.exit(f"Upload failed ({', '.join(reasons) or e.resp.status}): {e}")


if __name__ == "__main__":
    main()