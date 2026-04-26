import os
from pathlib import Path

import requests

SUBREDDITS = [
    "memes",
    "dankmemes",
    "funny",
]

MIN_UPVOTES = 3000

LEGACY_STATE_FILE = Path("last_meme_state.txt")
SENT_HISTORY_FILE = Path("sent_meme_history.txt")
MEME_API_BASE = "https://meme-api.com/gimme"
MEMES_PER_SUBREDDIT = 20


def get_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_sent_post_ids() -> set[str]:
    sent_ids = set()

    if SENT_HISTORY_FILE.exists():
        for line in SENT_HISTORY_FILE.read_text(encoding="utf-8").splitlines():
            post_id = line.strip()
            if post_id and not post_id.startswith("#"):
                sent_ids.add(post_id)

    if LEGACY_STATE_FILE.exists():
        legacy_post_id = LEGACY_STATE_FILE.read_text(encoding="utf-8").strip()
        if legacy_post_id:
            sent_ids.add(legacy_post_id)

    return sent_ids


def save_sent_post_id(post_id: str) -> None:
    sent_ids = load_sent_post_ids()
    sent_ids.add(post_id)
    SENT_HISTORY_FILE.write_text("\n".join(sorted(sent_ids)) + "\n", encoding="utf-8")


def fetch_memes_from_subreddit(subreddit: str) -> list[dict]:
    url = f"{MEME_API_BASE}/{subreddit}/{MEMES_PER_SUBREDDIT}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []

    if isinstance(data, dict) and isinstance(data.get("memes"), list):
        return data["memes"]
    if isinstance(data, dict):
        return [data]
    return []


def is_usable_meme(data: dict) -> bool:
    if not data:
        return False
    if data.get("nsfw"):
        return False
    if data.get("spoiler"):
        return False
    if data.get("url", "").lower().endswith(".gif"):
        return False
    if int(data.get("ups", 0)) < MIN_UPVOTES:
        return False
    required_fields = ["url", "title", "postLink", "subreddit"]
    return all(data.get(field) for field in required_fields)


def extract_post_id(post_link: str) -> str:
    return post_link.rstrip("/").split("/")[-1] if post_link else ""


def pick_best_meme() -> dict:
    sent_post_ids = load_sent_post_ids()
    candidates = []
    seen_in_run = set()

    for subreddit in SUBREDDITS:
        for meme in fetch_memes_from_subreddit(subreddit):
            if not is_usable_meme(meme):
                continue

            post_id = extract_post_id(meme.get("postLink", ""))
            if not post_id:
                continue
            if post_id in sent_post_ids or post_id in seen_in_run:
                continue

            seen_in_run.add(post_id)
            candidates.append(
                {
                    "id": post_id,
                    "title": meme["title"],
                    "subreddit": meme["subreddit"],
                    "image_url": meme["url"],
                    "post_link": meme["postLink"],
                    "score": int(meme.get("ups", 0)),
                }
            )

    if not candidates:
        raise RuntimeError("No suitable unsent meme image found today.")

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
    caption = f"Daily meme\nr/{post['subreddit']} - {post['title']}"

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
    save_sent_post_id(best_post["id"])
    image_path = download_image(best_post["image_url"], Path("daily-meme"))

    send_to_discord(webhook_url, image_path, best_post)


if __name__ == "__main__":
    main()
