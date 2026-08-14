from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
USER_DATA_DIR = BASE_DIR / "browser_data"

AFFILIATE_URL = "https://affiliate.tiktok.com/"


def main():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=False,
            viewport={"width": 1440, "height": 900},
        )

        page = context.pages[0] if context.pages else context.new_page()

        page.goto(
            AFFILIATE_URL,
            wait_until="domcontentloaded",
            timeout=120_000,
        )

        print("")
        print("========================================")
        print("Trinh duyet da mo.")
        print("Hay dang nhap TikTok Shop Affiliate.")
        print("Sau khi dang nhap thanh cong, quay lai Terminal.")
        print("Nhan ENTER de luu session va dong trinh duyet.")
        print("========================================")
        print("")

        input()

        context.close()


if __name__ == "__main__":
    main()