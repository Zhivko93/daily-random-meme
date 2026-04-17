import os
from pathlib import Path

import requests

SUBREDDITS = [
    "memes",
    "dankmemes",
    "wholesomememes",
    "me_irl",
    "funny",
]

STATE_FILE = Path("last_meme_state.txt")
MEME_API_BASE = "https://meme-api.com/gimme"


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


def fetch_meme_from_subreddit(subreddit: str) -> dict | None:
    url = f"{MEME_API_BASE}/{subreddit}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None

    if not data:
        return None

    # Skip bad payloads
    if data.get("nsfw"):
        return None
    if data.get("spoiler"):
        return None
    if data.get("url", "").endswith(".gif"):
        return None
    if not data.get("url"):
        return None
    if not data.get("title"):
        return None
    if not data.get("postLink"):
        return None
    if not data.get("subreddit"):
        return None

    return data


def pick_best_meme() -> dict:
    last_post_id = load_last_post_id()
    candidates = []

    for subreddit in SUBREDDITS:
        meme = fetch_meme_from_subreddit(subreddit)
        if not meme:
            continue

        post_link = meme.get("postLink", "")
        post_id = post_link.rstrip("/").split("/")[-1] if post_link else ""
        if not post_id:
            continue
        if last_post_id and post_id == last_post_id:
            continue

        score = int(meme.get("ups", 0))

        candidates.append(
            {
                "id": post_id,
                "title": meme["title"],
                "subreddit": meme["subreddit"],
                "image_url": meme["url"],
                "post_link": meme["postLink"],
                "score": score,
            }
        )

    if not candidates:
        raise RuntimeError("No suitable meme image found today.")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0]


def download_image(url: str, output_path: Path) -> Path:
    response = requests.get(url, timeout=30)
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
    return final_path


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
    image_path = download_image(best_post["image_url"], Path("daily-meme"))

    send_to_discord(webhook_url, image_path, best_post)
    save_last_post_id(best_post["id"])


if __name__ == "__main__":
    main()
