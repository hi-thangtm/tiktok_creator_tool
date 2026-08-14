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
            viewport={
                "width": 1440,
                "height": 900,
            },
        )

        page = (
            context.pages[0]
            if context.pages
            else context.new_page()
        )

        page.goto(
            AFFILIATE_URL,
            wait_until="domcontentloaded",
            timeout=120_000,
        )

        print("")
        print("========================================")
        print("DETECT SCROLL CONTAINER")
        print("========================================")
        print("")
        print("1. Vao trang Tim nha sang tao")
        print("2. Ap dung bo loc")
        print("3. Cho danh sach hien ra")
        print("4. Quay lai Terminal")
        print("")

        input("Nhan ENTER de bat dau detect...")

        # Gan ID tam cho tat ca element co kha nang scroll
        result = page.evaluate(
            """
            () => {
                const elements = [
                    document.documentElement,
                    document.body,
                    ...document.querySelectorAll('*')
                ];

                const result = [];

                for (let i = 0; i < elements.length; i++) {
                    const el = elements[i];

                    if (!el) continue;

                    const style = getComputedStyle(el);

                    const canScrollY =
                        el.scrollHeight > el.clientHeight + 5;

                    if (!canScrollY) continue;

                    el.dataset.scrollDetectId = String(i);

                    result.push({
                        id: String(i),
                        tag: el.tagName,
                        className:
                            typeof el.className === 'string'
                                ? el.className
                                : '',
                        scrollTop: el.scrollTop,
                        scrollHeight: el.scrollHeight,
                        clientHeight: el.clientHeight,
                        overflowY: style.overflowY,
                    });
                }

                return result;
            }
            """
        )

        print("")
        print(
            "Tim thay",
            len(result),
            "element co the scroll."
        )
        print("")
        print(
            "Bay gio hay dung CHUOT cuon xuong danh sach "
            "creator 2-3 lan."
        )
        print(
            "Sau do quay lai Terminal."
        )

        input(
            "\nSau khi da cuon bang tay, nhan ENTER..."
        )

        changed = page.evaluate(
            """
            () => {
                const result = [];

                const elements =
                    document.querySelectorAll(
                        '[data-scroll-detect-id]'
                    );

                for (const el of elements) {
                    const id =
                        el.dataset.scrollDetectId;

                    if (!id) continue;

                    if (el.scrollTop > 0) {
                        result.push({
                            id,
                            tag: el.tagName,
                            className:
                                typeof el.className === 'string'
                                    ? el.className
                                    : '',
                            scrollTop: el.scrollTop,
                            scrollHeight: el.scrollHeight,
                            clientHeight: el.clientHeight,
                            overflowY:
                                getComputedStyle(el).overflowY,
                        });
                    }
                }

                return result;
            }
            """
        )

        print("")
        print("========================================")
        print("ELEMENT DA SCROLL")
        print("========================================")

        if not changed:
            print("Khong detect duoc element nao.")

        for item in changed:
            print("")
            print("ID:", item["id"])
            print("TAG:", item["tag"])
            print(
                "CLASS:",
                item["className"][:200],
            )
            print(
                "scrollTop:",
                item["scrollTop"],
            )
            print(
                "scrollHeight:",
                item["scrollHeight"],
            )
            print(
                "clientHeight:",
                item["clientHeight"],
            )
            print(
                "overflowY:",
                item["overflowY"],
            )

        print("")
        print("========================================")

        input(
            "\nNhan ENTER de dong trinh duyet..."
        )

        context.close()


if __name__ == "__main__":
    main()