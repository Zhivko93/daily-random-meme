import os
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

USER_AGENT = "daily-meme-discord-bot/1.1"
SUBREDDITS = [
    "memes",
    "dankmemes",
    "wholesomememes",
    "me_irl",
    "funny",
]
HEADERS = {"User-Agent": USER_AGENT}

STATE_FILE = Path("last_meme_state.txt")
MIN_SCORE = 50


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


def extract_gallery_image_url(post: dict) -> str | None:
    media_metadata = post.get("media_metadata") or {}
    if not isinstance(media_metadata, dict):
        return None

    for _, item in media_metadata.items():
        if not isinstance(item, dict):
            continue

        s = item.get("s") or {}
        url = s.get("u") or s.get("gif")
        if url:
            return url.replace("&amp;", "&")

    return None


def extract_preview_image_url(post: dict) -> str | None:
    preview = post.get("preview") or {}
    images = preview.get("images") or []
    if not images:
        return None

    source = images[0].get("source") or {}
    url = source.get("url")
    if url:
        return url.replace("&amp;", "&")

    return None


def normalize_image_url(post: dict) -> str | None:
    url = (post.get("url_overridden_by_dest") or post.get("url") or "").strip()
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    # Direct image links
    if is_image_url(url):
        return url

    # Reddit preview
    if host == "preview.redd.it":
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    # Direct reddit image hosting
    if host == "i.redd.it":
        return url

    # Gallery fallback
    gallery_url = extract_gallery_image_url(post)
    if gallery_url:
        return gallery_url

    # Preview fallback
    preview_url = extract_preview_image_url(post)
    if preview_url:
        return preview_url

    return None


def fetch_subreddit_posts(subreddit: str, listing: str = "top", time_filter: str = "day") -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit}/{listing}.json"
    params = {
        "limit": 50,
        "raw_json": 1,
    }
    if listing == "top":
        params["t"] = time_filter

    response = requests.get(url, headers=HEADERS, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    children = data.get("data", {}).get("children", [])
    return [child.get("data", {}) for child in children]


def score_post(post: dict, now_ts: float) -> float:
    base_score = float(post.get("score", 0))
    created = float(post.get("created_utc", 0))
    age_hours = max((now_ts - created) / 3600.0, 1.0)
    return base_score / (age_hours ** 0.8)


def collect_candidates(listing: str, time_filter: str | None, last_post_id: str | None) -> list[dict]:
    now_ts = time.time()
    candidates = []

    for subreddit in SUBREDDITS:
        try:
            posts = fetch_subreddit_posts(
                subreddit=subreddit,
                listing=listing,
                time_filter=time_filter or "day",
            )
        except Exception:
            continue

        for post in posts:
            post_id = post.get("id")
            title = post.get("title", "").strip()
            subreddit_name = post.get("subreddit", subreddit)
            over_18 = post.get("over_18", False)
            is_video = post.get("is_video", False)
            score = int(post.get("score", 0))
            permalink = post.get("permalink", "")

            if not post_id or not title:
                continue
            if last_post_id and post_id == last_post_id:
                continue
            if over_18:
                continue
            if is_video:
                continue
            if score < MIN_SCORE:
                continue

            image_url = normalize_image_url(post)
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

    return candidates


def pick_best_meme() -> dict:
    last_post_id = load_last_post_id()

    # First try: top of day
    candidates = collect_candidates("top", "day", last_post_id)

    # Fallback: hot posts
    if not candidates:
        candidates = collect_candidates("hot", None, last_post_id)

    if not candidates:
        raise RuntimeError("No suitable meme image found today.")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0]


def download_image(url: str, output_path: Path) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()
    if "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"
    else:
        ext = ".jpg"

    final_path = output_path.with_suffix(ext)
    final_path.write_bytes(response.content)
    return str(final_path)


def send_to_discord(webhook_url: str, image_path: Path, post: dict) -> None:
    caption = f"Daily meme\nr/{post['subreddit']} • {post['title']}"

    with open(image_path, "rb") as f:
        response = requests.post(
            webhook_url,
            data={"content": caption},
            files={"file": (image_path.name, f, "application/octet-stream")},
            timeout=60,
        )

    if response.status_code >= 300:
        raise RuntimeError(f"Discord webhook error: {response.status_code} {response.text}")


def main() -> None:
    webhook_url = get_env("DISCORD_RANDOM_WEBHOOK_URL")

    best_post = pick_best_meme()
    base_path = Path("daily-meme")
    downloaded_path = Path(download_image(best_post["image_url"], base_path))

    send_to_discord(webhook_url, downloaded_path, best_post)
    save_last_post_id(best_post["id"])


if __name__ == "__main__":
    main()
