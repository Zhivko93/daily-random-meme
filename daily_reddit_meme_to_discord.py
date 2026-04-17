import os
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

USER_AGENT = "daily-meme-discord-bot/1.0"
SUBREDDITS = [
    "memes",
    "dankmemes",
    "wholesomememes",
    "me_irl",
]
HEADERS = {"User-Agent": USER_AGENT}

ALLOWED_IMAGE_HOSTS = {
    "i.redd.it",
    "i.imgur.com",
    "imgur.com",
    "preview.redd.it",
}

STATE_FILE = Path("last_meme_state.txt")


def get_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_last_post_id() -> str | None:
    if STATE_FILE.exists():
        return STATE_FILE.read_text(encoding="utf-8").strip() or None
    return None


def save_last_post_id(post_id: str) -> None:
    STATE_FILE.write_text(post_id, encoding="utf-8")


def is_image_url(url: str) -> bool:
    lower = url.lower()
    return lower.endswith(".jpg") or lower.endswith(".jpeg") or lower.endswith(".png") or lower.endswith(".webp")


def normalize_image_url(url: str) -> str | None:
    if not url:
        return None

    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if host not in ALLOWED_IMAGE_HOSTS:
        return None

    if host == "preview.redd.it":
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    if is_image_url(url):
        return url

    return None


def fetch_subreddit_posts(subreddit: str) -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit}/top.json"
    params = {
        "t": "day",
        "limit": 25,
    }
    response = requests.get(url, headers=HEADERS, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    children = data.get("data", {}).get("children", [])
    return [child.get("data", {}) for child in children]


def score_post(post: dict, now_ts: float) -> float:
    score = float(post.get("score", 0))
    created = float(post.get("created_utc", 0))
    age_hours = max((now_ts - created) / 3600.0, 1.0)

    # Strong but simple balance of popularity and recency
    return score / (age_hours ** 0.8)


def pick_best_meme() -> dict:
    now_ts = time.time()
    last_post_id = load_last_post_id()
    candidates = []

    for subreddit in SUBREDDITS:
        try:
            posts = fetch_subreddit_posts(subreddit)
        except Exception:
            continue

        for post in posts:
            post_id = post.get("id")
            title = post.get("title", "").strip()
            subreddit_name = post.get("subreddit", subreddit)
            over_18 = post.get("over_18", False)
            is_video = post.get("is_video", False)
            is_gallery = bool(post.get("is_gallery"))
            post_hint = post.get("post_hint", "")
            permalink = post.get("permalink", "")
            url = post.get("url_overridden_by_dest") or post.get("url") or ""

            if not post_id or not title:
                continue
            if post_id == last_post_id:
                continue
            if over_18:
                continue
            if is_video:
                continue
            if is_gallery:
                continue
            if post_hint not in {"image", "link"}:
                continue

            image_url = normalize_image_url(url)
            if not image_url:
                continue

            candidates.append(
                {
                    "id": post_id,
                    "title": title,
                    "subreddit": subreddit_name,
                    "image_url": image_url,
                    "permalink": f"https://www.reddit.com{permalink}",
                    "score": score_post(post, now_ts),
                }
            )

    if not candidates:
        raise RuntimeError("No suitable meme image found today.")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0]


def download_image(url: str, output_path: Path) -> None:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    output_path.write_bytes(response.content)


def send_to_discord(webhook_url: str, image_path: Path, post: dict) -> None:
    caption = f"Daily meme\nr/{post['subreddit']} • {post['title']}"

    with open(image_path, "rb") as f:
        response = requests.post(
            webhook_url,
            data={"content": caption},
            files={"file": (image_path.name, f, "image/jpeg")},
            timeout=60,
        )

    if response.status_code >= 300:
        raise RuntimeError(f"Discord webhook error: {response.status_code} {response.text}")


def main() -> None:
    webhook_url = get_env("DISCORD_RANDOM_WEBHOOK_URL")

    best_post = pick_best_meme()
    image_path = Path("daily-meme.jpg")

    download_image(best_post["image_url"], image_path)
    send_to_discord(webhook_url, image_path, best_post)
    save_last_post_id(best_post["id"])


if __name__ == "__main__":
    main()
