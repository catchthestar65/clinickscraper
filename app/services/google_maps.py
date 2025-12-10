"""Google Mapsスクレイピングサービス"""

import logging
import re
import time
from contextlib import contextmanager
from typing import Generator, Any

from playwright.sync_api import sync_playwright, Browser, Page, Playwright
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import config
from app.exceptions import ScrapingError
from app.models.clinic import Clinic

logger = logging.getLogger(__name__)


class GoogleMapsScraper:
    """Google Mapsからクリニック情報をスクレイピング"""

    BASE_URL = "https://www.google.com/maps/search/"

    def __init__(self, headless: bool = True) -> None:
        """
        Args:
            headless: ヘッドレスモードで実行するか
        """
        self.headless = headless
        self.max_results = config.max_results_per_query
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    @contextmanager
    def _browser_context(self) -> Generator[Page, None, None]:
        """ブラウザコンテキストマネージャー"""
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=self.headless)
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

        try:
            yield page
        finally:
            context.close()
            browser.close()
            playwright.stop()

    def search(self, query: str, max_results: int | None = None) -> list[Clinic]:
        """
        検索クエリでGoogle Mapsを検索し、クリニック情報を取得

        Args:
            query: 検索クエリ（例: "新宿 AGA"）
            max_results: 最大取得件数（指定なしでデフォルト値使用）

        Returns:
            クリニック情報のリスト
        """
        max_results = max_results or self.max_results
        clinics: list[Clinic] = []

        logger.info(f"Starting search: {query} (max: {max_results})")

        with self._browser_context() as page:
            try:
                # Google Maps検索
                search_url = f"{self.BASE_URL}{query.replace(' ', '+')}"
                page.goto(search_url, wait_until="networkidle", timeout=60000)

                # クッキー同意ダイアログがあれば閉じる
                self._handle_consent_dialog(page)

                # 検索結果のスクロールで全件読み込み
                self._scroll_results(page, max_results)

                # 各クリニックの情報を取得
                results = page.query_selector_all('[data-result-index]')
                logger.info(f"Found {len(results)} results")

                for i, result in enumerate(results[:max_results]):
                    try:
                        clinic = self._extract_clinic_info(result, page, i)
                        if clinic:
                            clinics.append(clinic)
                    except Exception as e:
                        logger.warning(f"Error extracting clinic {i}: {e}")
                        continue

            except Exception as e:
                logger.error(f"Search error: {e}")
                raise ScrapingError(f"Google Maps検索中にエラーが発生しました: {e}")

        logger.info(f"Successfully extracted {len(clinics)} clinics")
        return clinics

    def _handle_consent_dialog(self, page: Page) -> None:
        """クッキー同意ダイアログを処理"""
        try:
            # 日本語の「同意する」ボタン
            consent_button = page.query_selector('button:has-text("同意する")')
            if consent_button:
                consent_button.click()
                time.sleep(1)
                logger.debug("Accepted consent dialog")
        except Exception:
            pass  # ダイアログがなければスキップ

    def _scroll_results(self, page: Page, max_results: int) -> None:
        """検索結果をスクロールして全件読み込み"""
        results_container = page.query_selector('[role="feed"]')
        if not results_container:
            logger.warning("Results container not found")
            return

        prev_count = 0
        scroll_attempts = 0
        max_attempts = 30

        while scroll_attempts < max_attempts:
            results = page.query_selector_all('[data-result-index]')
            current_count = len(results)

            if current_count >= max_results:
                logger.debug(f"Reached max results: {current_count}")
                break

            if current_count == prev_count:
                scroll_attempts += 1
                if scroll_attempts >= 3:
                    # 3回連続で変化なしなら終了
                    logger.debug("No more results to load")
                    break
            else:
                scroll_attempts = 0

            prev_count = current_count

            # スクロール実行
            results_container.evaluate("el => el.scrollTop = el.scrollHeight")
            time.sleep(1)

        logger.debug(f"Scroll completed, total results: {prev_count}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _extract_clinic_info(
        self, element: Any, page: Page, index: int
    ) -> Clinic | None:
        """
        検索結果要素からクリニック情報を抽出

        Args:
            element: 検索結果要素
            page: Playwrightページ
            index: 結果インデックス

        Returns:
            Clinicオブジェクト、抽出失敗時はNone
        """
        # クリックして詳細パネルを開く
        try:
            element.click()
            time.sleep(1.5)
        except Exception as e:
            logger.debug(f"Click failed for element {index}: {e}")
            return None

        # クリニック名
        name = self._get_text(page, "h1")
        if not name:
            logger.debug(f"No name found for element {index}")
            return None

        # 公式サイトURL
        url = self._get_website_url(page)

        # 住所
        address = self._get_text(page, '[data-item-id="address"] .fontBodyMedium')

        # 電話番号
        phone = self._get_phone(page)

        # 評価
        rating = self._get_rating(page)

        # 口コミ数
        reviews = self._get_reviews(page)

        # 所在地（区）を抽出
        area = self._extract_area(address)

        try:
            clinic = Clinic(
                name=name,
                url=url,
                address=address,
                phone=phone,
                rating=rating,
                reviews=reviews,
                area=area,
            )
            logger.debug(f"Extracted: {name}")
            return clinic
        except Exception as e:
            logger.warning(f"Failed to create Clinic object: {e}")
            return None

    def _get_text(self, page: Page, selector: str) -> str | None:
        """セレクタからテキストを取得"""
        try:
            element = page.query_selector(selector)
            if element:
                return element.inner_text().strip()
        except Exception:
            pass
        return None

    def _get_website_url(self, page: Page) -> str | None:
        """公式サイトURLを取得"""
        try:
            # 公式サイトリンクを探す
            website_button = page.query_selector('[data-item-id="authority"]')
            if website_button:
                href = website_button.get_attribute("href")
                if href:
                    return href

            # 代替: aタグでウェブサイトを探す
            website_link = page.query_selector('a[data-value="ウェブサイト"]')
            if website_link:
                href = website_link.get_attribute("href")
                if href:
                    return href
        except Exception:
            pass
        return None

    def _get_phone(self, page: Page) -> str | None:
        """電話番号を取得"""
        try:
            phone_el = page.query_selector('[data-item-id^="phone"]')
            if phone_el:
                text = phone_el.inner_text()
                # 数字とハイフンを抽出
                match = re.search(r"[\d\-]+", text)
                if match:
                    return match.group()
        except Exception:
            pass
        return None

    def _get_rating(self, page: Page) -> float | None:
        """評価を取得"""
        try:
            rating_el = page.query_selector('[role="img"][aria-label*="つ星"]')
            if rating_el:
                label = rating_el.get_attribute("aria-label")
                if label:
                    match = re.search(r"([\d.]+)", label)
                    if match:
                        return float(match.group(1))
        except Exception:
            pass
        return None

    def _get_reviews(self, page: Page) -> int | None:
        """口コミ数を取得"""
        try:
            reviews_el = page.query_selector('[aria-label*="件のクチコミ"]')
            if reviews_el:
                label = reviews_el.get_attribute("aria-label")
                if label:
                    match = re.search(r"([\d,]+)", label)
                    if match:
                        return int(match.group(1).replace(",", ""))
        except Exception:
            pass
        return None

    def _extract_area(self, address: str | None) -> str:
        """住所から区名を抽出"""
        if not address:
            return ""

        # 「〇〇区」パターンを抽出
        match = re.search(r"([^\s]+区)", address)
        if match:
            return match.group(1)

        # 「〇〇市」パターン
        match = re.search(r"([^\s]+市)", address)
        if match:
            return match.group(1)

        return ""
