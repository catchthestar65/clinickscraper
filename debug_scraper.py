"""Google Mapsスクレイピングのデバッグスクリプト"""

import sys
import time
from playwright.sync_api import sync_playwright


def debug_google_maps(query: str = "渋谷 AGA"):
    """Google Mapsの検索結果を調査"""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)  # ヘッドレスモード
        context = browser.new_context(
            locale="ja-JP",
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        # Google Maps検索
        search_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
        print(f"Opening: {search_url}")

        page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)  # ページ読み込み待機

        # スクリーンショット保存
        page.screenshot(path="debug_screenshot_1.png")
        print("Screenshot saved: debug_screenshot_1.png")

        # クッキー同意ダイアログの確認
        consent_button = page.query_selector('button:has-text("同意する")')
        if consent_button:
            print("Found consent button, clicking...")
            consent_button.click()
            time.sleep(2)
            page.screenshot(path="debug_screenshot_2.png")

        # h1の内容を確認
        h1 = page.query_selector("h1")
        if h1:
            print(f"h1 content: '{h1.inner_text()}'")
        else:
            print("No h1 found")

        # 検索結果リストのセレクタを確認
        print("\n=== Checking selectors ===")

        # 現在使用しているセレクタ
        results1 = page.query_selector_all('a[href*="/maps/place/"]')
        print(f'a[href*="/maps/place/"]: {len(results1)} elements')

        # 代替セレクタ
        results2 = page.query_selector_all('[role="feed"] > div')
        print(f'[role="feed"] > div: {len(results2)} elements')

        results3 = page.query_selector_all('div[role="article"]')
        print(f'div[role="article"]: {len(results3)} elements')

        results4 = page.query_selector_all('.Nv2PK')
        print(f'.Nv2PK: {len(results4)} elements')

        results5 = page.query_selector_all('[data-result-index]')
        print(f'[data-result-index]: {len(results5)} elements')

        # フィードコンテナの確認
        feed = page.query_selector('[role="feed"]')
        if feed:
            print("\n[role='feed'] found")
            # フィード内の要素を調査
            children = feed.query_selector_all(':scope > div')
            print(f"Direct children of feed: {len(children)}")
        else:
            print("\n[role='feed'] NOT found")

        # 最初の検索結果をクリックしてみる
        if results1:
            print(f"\n=== Clicking first result ===")
            first = results1[0]
            href = first.get_attribute("href")
            print(f"First result href: {href[:100]}..." if href else "No href")

            # aria-label属性を確認
            label = first.get_attribute("aria-label")
            print(f"First result aria-label: {label}")

            # クリック
            first.click()
            time.sleep(3)

            # クリック後のh1
            h1_after = page.query_selector("h1")
            if h1_after:
                print(f"h1 after click: '{h1_after.inner_text()}'")

            # スクリーンショット
            page.screenshot(path="debug_screenshot_3.png")
            print("Screenshot saved: debug_screenshot_3.png")

            # 詳細パネルの情報を取得
            print("\n=== Detail panel info ===")

            # ウェブサイトボタン
            website = page.query_selector('[data-item-id="authority"]')
            if website:
                print(f"Website URL: {website.get_attribute('href')}")
            else:
                print("Website button not found with [data-item-id='authority']")

                # 代替セレクタ
                website2 = page.query_selector('a[data-value="ウェブサイト"]')
                if website2:
                    print(f"Website (alt): {website2.get_attribute('href')}")
                else:
                    print("Website button not found with a[data-value='ウェブサイト']")

            # 住所
            address = page.query_selector('[data-item-id="address"] .fontBodyMedium')
            if address:
                print(f"Address: {address.inner_text()}")
            else:
                print("Address not found")

            # 電話番号
            phone = page.query_selector('[data-item-id^="phone"]')
            if phone:
                print(f"Phone: {phone.inner_text()}")
            else:
                print("Phone not found")

        print("\n=== Debug complete ===")
        browser.close()


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "渋谷 AGA"
    debug_google_maps(query)
