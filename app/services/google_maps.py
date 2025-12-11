"""Google Mapsスクレイピングサービス（Async版）"""

import asyncio
import logging
import os
import re
import time
import traceback
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any

from playwright.async_api import async_playwright, Browser, Page, Playwright, TimeoutError as PlaywrightTimeout
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import config
from app.exceptions import ScrapingError
from app.models.clinic import Clinic

logger = logging.getLogger(__name__)


def _get_memory_usage_mb() -> float:
    """現在のメモリ使用量をMB単位で取得"""
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        # macOSはbytes、Linuxはkilobytes
        if os.uname().sysname == "Darwin":
            return rusage.ru_maxrss / (1024 * 1024)
        else:
            return rusage.ru_maxrss / 1024
    except Exception:
        return 0.0


def _log_memory(context: str) -> None:
    """メモリ使用量をログ出力"""
    mem_mb = _get_memory_usage_mb()
    logger.info(f"[MEMORY] {context}: {mem_mb:.1f} MB")


class GoogleMapsScraper:
    """Google Mapsからクリニック情報をスクレイピング（Async版）"""

    BASE_URL = "https://www.google.com/maps/search/"

    def __init__(self, headless: bool = True) -> None:
        """
        Args:
            headless: ヘッドレスモードで実行するか
        """
        self.headless = headless
        self.max_results = config.max_results_per_query

    @asynccontextmanager
    async def _browser_context(self) -> AsyncGenerator[Page, None]:
        """ブラウザコンテキストマネージャー（Async版）"""
        _log_memory("ブラウザ起動前")
        logger.info("[BROWSER] Playwright開始...")

        playwright = None
        browser = None
        context = None

        try:
            playwright = await async_playwright().start()
            logger.info("[BROWSER] Chromium起動中...")

            # メモリ使用量を削減するブラウザ引数
            browser = await playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--single-process",
                    "--disable-setuid-sandbox",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--disable-translate",
                    "--no-first-run",
                    "--disable-features=site-per-process",
                    "--js-flags=--max-old-space-size=256",
                ],
            )

            _log_memory("Chromium起動後")
            logger.info("[BROWSER] コンテキスト作成中...")

            context = await browser.new_context(
                locale="ja-JP",
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            logger.info("[BROWSER] ページ作成完了")
            _log_memory("ページ作成後")

            yield page

        except Exception as e:
            logger.error(f"[BROWSER] ブラウザ初期化エラー: {type(e).__name__}: {e}")
            logger.error(f"[BROWSER] スタックトレース:\n{traceback.format_exc()}")
            raise
        finally:
            logger.info("[BROWSER] クリーンアップ開始...")
            cleanup_start = time.time()

            # Async版のクリーンアップ（短いタイムアウトでメモリ解放を優先）
            # タイムアウト: 各2秒（合計最大6秒）
            try:
                if context:
                    try:
                        await asyncio.wait_for(context.close(), timeout=2.0)
                        logger.info("[BROWSER] コンテキスト終了完了")
                    except asyncio.TimeoutError:
                        logger.warning("[BROWSER] コンテキスト終了タイムアウト(2秒)")
                    except Exception as e:
                        logger.warning(f"[BROWSER] コンテキスト終了エラー: {e}")

                if browser:
                    try:
                        await asyncio.wait_for(browser.close(), timeout=2.0)
                        logger.info("[BROWSER] ブラウザ終了完了")
                    except asyncio.TimeoutError:
                        logger.warning("[BROWSER] ブラウザ終了タイムアウト(2秒)")
                    except Exception as e:
                        logger.warning(f"[BROWSER] ブラウザ終了エラー: {e}")

                if playwright:
                    try:
                        await asyncio.wait_for(playwright.stop(), timeout=2.0)
                        logger.info("[BROWSER] Playwright停止完了")
                    except asyncio.TimeoutError:
                        logger.warning("[BROWSER] Playwright停止タイムアウト(2秒)")
                    except Exception as e:
                        logger.warning(f"[BROWSER] Playwright停止エラー: {e}")
            except Exception as e:
                logger.error(f"[BROWSER] クリーンアップ中のエラー: {e}")

            cleanup_elapsed = time.time() - cleanup_start
            logger.info(f"[BROWSER] クリーンアップ完了 ({cleanup_elapsed:.1f}秒)")
            _log_memory("ブラウザ終了後")

    def search(self, query: str, max_results: int | None = None) -> list[Clinic]:
        """
        検索クエリでGoogle Mapsを検索し、クリニック情報を取得
        （同期インターフェース - 内部でasyncio.runを使用）

        Args:
            query: 検索クエリ（例: "新宿 AGA"）
            max_results: 最大取得件数（指定なしでデフォルト値使用）

        Returns:
            クリニック情報のリスト
        """
        return asyncio.run(self._search_async(query, max_results))

    async def _search_async(self, query: str, max_results: int | None = None) -> list[Clinic]:
        """検索の非同期実装"""
        max_results = max_results or self.max_results
        clinics: list[Clinic] = []
        start_time = time.time()

        logger.info(f"[SEARCH] 検索開始: '{query}' (最大: {max_results}件)")
        _log_memory("検索開始")

        async with self._browser_context() as page:
            try:
                # Google Maps検索
                search_url = f"{self.BASE_URL}{query.replace(' ', '+')}"
                logger.info(f"[SEARCH] URL: {search_url}")
                logger.info("[SEARCH] ページ読み込み中...")

                try:
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    logger.info("[SEARCH] ページ読み込み完了 (domcontentloaded)")
                except PlaywrightTimeout as e:
                    logger.error(f"[SEARCH] ページ読み込みタイムアウト (60秒): {e}")
                    raise ScrapingError(f"ページ読み込みタイムアウト: {query}")
                except Exception as e:
                    logger.error(f"[SEARCH] ページ読み込みエラー: {type(e).__name__}: {e}")
                    logger.error(f"[SEARCH] スタックトレース:\n{traceback.format_exc()}")
                    raise

                # ページが完全に読み込まれるまで待機
                logger.info("[SEARCH] 追加待機 (3秒)...")
                await asyncio.sleep(3)
                _log_memory("ページ読み込み後")

                # クッキー同意ダイアログがあれば閉じる
                await self._handle_consent_dialog(page)

                # 単一結果ページかどうかをチェック
                feed = await page.query_selector('[role="feed"]')
                h1_el = await page.query_selector("h1")
                h1_text = await h1_el.inner_text() if h1_el else ""
                logger.info(f"[SEARCH] ページ解析: feed={feed is not None}, h1='{h1_text}'")

                # 単一結果ページの場合、クエリを修正してリトライ
                if not feed and h1_text and h1_text != "結果":
                    logger.warning(f"[SEARCH] 単一結果ページ検出: {h1_text}")

                    retry_keywords = ["クリニック", "病院", "医院"]
                    should_retry = not any(kw in query for kw in retry_keywords)

                    if should_retry:
                        modified_query = f"{query} クリニック"
                        logger.info(f"[SEARCH] クエリを修正してリトライ: '{modified_query}'")

                        retry_url = f"{self.BASE_URL}{modified_query.replace(' ', '+')}"
                        try:
                            await page.goto(retry_url, wait_until="domcontentloaded", timeout=60000)
                            await asyncio.sleep(3)
                            await self._handle_consent_dialog(page)

                            feed = await page.query_selector('[role="feed"]')
                            h1_el = await page.query_selector("h1")
                            h1_text = await h1_el.inner_text() if h1_el else ""
                            logger.info(f"[SEARCH] リトライ後ページ解析: feed={feed is not None}, h1='{h1_text}'")
                        except Exception as e:
                            logger.warning(f"[SEARCH] リトライ失敗: {e}")

                # 最終的なページタイプに基づいて処理
                if not feed and h1_text and h1_text != "結果":
                    logger.info(f"[SEARCH] 単一結果として抽出: {h1_text}")
                    clinic = await self._extract_single_result(page, h1_text)
                    if clinic:
                        clinics.append(clinic)
                        logger.info(f"[SEARCH] 単一結果を抽出: {clinic.name}")
                else:
                    logger.info("[SEARCH] 複数結果ページ - スクロール開始...")
                    await self._scroll_results(page, max_results)
                    _log_memory("スクロール後")

                    results = await page.query_selector_all('a[href*="/maps/place/"]')
                    logger.info(f"[SEARCH] 検索結果: {len(results)}件発見")

                    for i, result in enumerate(results[:max_results]):
                        try:
                            if i > 0 and i % 10 == 0:
                                _log_memory(f"抽出中 ({i}件目)")

                            clinic = await self._extract_clinic_info(result, page, i)
                            if clinic:
                                clinics.append(clinic)
                        except Exception as e:
                            logger.warning(f"[SEARCH] クリニック {i} 抽出エラー: {type(e).__name__}: {e}")
                            continue

            except ScrapingError:
                raise
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"[SEARCH] 検索エラー ({elapsed:.1f}秒経過): {type(e).__name__}: {e}")
                logger.error(f"[SEARCH] スタックトレース:\n{traceback.format_exc()}")
                _log_memory("エラー発生時")
                raise ScrapingError(f"Google Maps検索中にエラーが発生しました: {type(e).__name__}: {e}")

        elapsed = time.time() - start_time
        logger.info(f"[SEARCH] 検索完了: {len(clinics)}件抽出 ({elapsed:.1f}秒)")
        _log_memory("検索完了")
        return clinics

    async def _handle_consent_dialog(self, page: Page) -> None:
        """クッキー同意ダイアログを処理"""
        try:
            consent_button = await page.query_selector('button:has-text("同意する")')
            if consent_button:
                await consent_button.click()
                await asyncio.sleep(1)
                logger.debug("Accepted consent dialog")
        except Exception:
            pass

    async def _extract_single_result(self, page: Page, name: str) -> Clinic | None:
        """単一結果ページからクリニック情報を抽出"""
        logger.debug(f"Extracting single result: {name}")

        url = await self._get_website_url(page)

        if not url:
            website_link = await page.query_selector('a[data-item-id="authority"]')
            if website_link:
                url = await website_link.get_attribute("href")

        address = await self._get_text(page, '[data-item-id="address"] .fontBodyMedium')
        if not address:
            address_el = await page.query_selector('button[data-item-id="address"]')
            if address_el:
                address = await address_el.inner_text()

        phone = await self._get_phone(page)
        rating = await self._get_rating(page)
        reviews = await self._get_reviews(page)
        area = self._extract_area(address)

        logger.info(f"Extracted single result: name={name}, url={url}, area={area}")

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
            return clinic
        except Exception as e:
            logger.warning(f"Failed to create Clinic object for single result: {e}")
            return None

    async def _scroll_results(self, page: Page, max_results: int) -> None:
        """検索結果をスクロールして全件読み込み"""
        results_container = await page.query_selector('[role="feed"]')
        if not results_container:
            logger.warning("Results container not found")
            return

        prev_count = 0
        scroll_attempts = 0
        max_attempts = 30

        while scroll_attempts < max_attempts:
            results = await page.query_selector_all('a[href*="/maps/place/"]')
            current_count = len(results)

            if current_count >= max_results:
                logger.debug(f"Reached max results: {current_count}")
                break

            if current_count == prev_count:
                scroll_attempts += 1
                if scroll_attempts >= 3:
                    logger.debug("No more results to load")
                    break
            else:
                scroll_attempts = 0

            prev_count = current_count

            await results_container.evaluate("el => el.scrollTop = el.scrollHeight")
            await asyncio.sleep(1)

        logger.debug(f"Scroll completed, total results: {prev_count}")

    async def _extract_clinic_info(
        self, element: Any, page: Page, index: int
    ) -> Clinic | None:
        """検索結果要素からクリニック情報を抽出"""
        extract_start = time.time()

        name = await element.get_attribute("aria-label")
        if not name:
            logger.debug(f"[EXTRACT][{index}] aria-labelなし、スキップ")
            return None

        logger.info(f"[EXTRACT][{index}] 開始: '{name}'")

        prev_h1 = await self._get_text(page, "h1.DUwDvf")
        logger.debug(f"[EXTRACT][{index}] クリック前 h1.DUwDvf='{prev_h1}'")

        click_success = await self._click_element_robust(element, page, index, name)
        if not click_success:
            return None

        # パネル更新待機
        max_wait_attempts = 15
        panel_ready = False

        for attempt in range(max_wait_attempts):
            current_h1 = await self._get_text(page, "h1.DUwDvf")

            if current_h1 and self._names_match(current_h1, name):
                logger.debug(f"[EXTRACT][{index}] パネル更新確認 (attempt={attempt+1}): h1='{current_h1}'")
                panel_ready = True
                break

            if current_h1 and current_h1 != prev_h1:
                logger.debug(f"[EXTRACT][{index}] h1変更検出 (attempt={attempt+1}): '{prev_h1}' -> '{current_h1}'")
                await asyncio.sleep(0.2)
                final_h1 = await self._get_text(page, "h1.DUwDvf")
                if final_h1 and self._names_match(final_h1, name):
                    logger.debug(f"[EXTRACT][{index}] 最終確認OK: h1='{final_h1}'")
                    panel_ready = True
                    break

            await asyncio.sleep(0.2)

        if not panel_ready:
            final_h1 = await self._get_text(page, "h1.DUwDvf")
            logger.warning(f"[EXTRACT][{index}] パネル更新タイムアウト: 期待='{name}', 実際h1='{final_h1}'")

            if not final_h1:
                logger.info(f"[EXTRACT][{index}] JavaScriptクリックで再試行...")
                try:
                    await element.evaluate("el => el.click()")
                    await asyncio.sleep(1.0)
                    final_h1 = await self._get_text(page, "h1.DUwDvf")
                    if final_h1 and self._names_match(final_h1, name):
                        logger.info(f"[EXTRACT][{index}] 再クリック成功: h1='{final_h1}'")
                        panel_ready = True
                except Exception as e:
                    logger.debug(f"[EXTRACT][{index}] 再クリック失敗: {e}")

            if not panel_ready:
                final_h1 = await self._get_text(page, "h1.DUwDvf")
                if final_h1 and not self._names_match(final_h1, name):
                    logger.warning(f"[EXTRACT][{index}] 名前不一致のためスキップ: h1='{final_h1}'")
                    return None
                logger.info(f"[EXTRACT][{index}] パネル未確認だがデータ収集を試行")
                await asyncio.sleep(0.5)

        await asyncio.sleep(0.3)

        url = await self._get_website_url(page)
        logger.debug(f"[EXTRACT][{index}] URL取得: {url}")

        address = await self._get_text(page, '[data-item-id="address"] .fontBodyMedium')
        logger.debug(f"[EXTRACT][{index}] 住所取得: {address}")

        phone = await self._get_phone(page)
        logger.debug(f"[EXTRACT][{index}] 電話番号取得: {phone}")

        rating = await self._get_rating(page)
        reviews = await self._get_reviews(page)
        area = self._extract_area(address)

        extract_elapsed = time.time() - extract_start
        logger.info(f"[EXTRACT][{index}] 完了 ({extract_elapsed:.2f}秒): name='{name}', url={url}, area={area}")

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
            return clinic
        except Exception as e:
            logger.warning(f"[EXTRACT][{index}] Clinicオブジェクト作成失敗: {e}")
            return None

    async def _click_element_robust(
        self, element: Any, page: Page, index: int, name: str
    ) -> bool:
        """要素を確実にクリックする（複数の方法を試行）"""
        try:
            await element.scroll_into_view_if_needed()
            await asyncio.sleep(0.2)
            logger.debug(f"[EXTRACT][{index}] スクロール完了")
        except Exception as e:
            logger.debug(f"[EXTRACT][{index}] スクロール失敗: {e}")

        try:
            await element.click(timeout=5000)
            logger.debug(f"[EXTRACT][{index}] 通常クリック成功")
            return True
        except Exception as e:
            logger.debug(f"[EXTRACT][{index}] 通常クリック失敗: {type(e).__name__}: {e}")

        try:
            await element.click(force=True, timeout=5000)
            logger.debug(f"[EXTRACT][{index}] 強制クリック成功")
            return True
        except Exception as e:
            logger.debug(f"[EXTRACT][{index}] 強制クリック失敗: {type(e).__name__}: {e}")

        try:
            await element.evaluate("el => el.click()")
            logger.debug(f"[EXTRACT][{index}] JSクリック成功")
            return True
        except Exception as e:
            logger.debug(f"[EXTRACT][{index}] JSクリック失敗: {type(e).__name__}: {e}")

        try:
            await element.evaluate("""el => {
                const event = new MouseEvent('click', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                });
                el.dispatchEvent(event);
            }""")
            logger.debug(f"[EXTRACT][{index}] イベント発火成功")
            return True
        except Exception as e:
            logger.debug(f"[EXTRACT][{index}] イベント発火失敗: {type(e).__name__}: {e}")

        try:
            box = await element.bounding_box()
            if box:
                x = box["x"] + box["width"] / 2
                y = box["y"] + box["height"] / 2
                await page.mouse.click(x, y)
                logger.debug(f"[EXTRACT][{index}] 座標クリック成功: ({x}, {y})")
                return True
        except Exception as e:
            logger.debug(f"[EXTRACT][{index}] 座標クリック失敗: {type(e).__name__}: {e}")

        logger.warning(f"[EXTRACT][{index}] 全クリック方法失敗: '{name}'")
        return False

    def _names_match(self, h1_name: str, aria_label_name: str) -> bool:
        """h1のクリニック名とaria-labelの名前が一致するか判定"""
        if not h1_name or not aria_label_name:
            return False

        if h1_name == aria_label_name:
            return True

        h1_normalized = h1_name.replace(" ", "").replace("　", "").lower()
        aria_normalized = aria_label_name.replace(" ", "").replace("　", "").lower()

        if h1_normalized == aria_normalized:
            return True

        if h1_normalized in aria_normalized or aria_normalized in h1_normalized:
            return True

        min_len = min(len(h1_normalized), len(aria_normalized))
        if min_len >= 5 and h1_normalized[:min_len] == aria_normalized[:min_len]:
            return True

        return False

    async def _get_text(self, page: Page, selector: str) -> str | None:
        """セレクタからテキストを取得"""
        try:
            element = await page.query_selector(selector)
            if element:
                return (await element.inner_text()).strip()
        except Exception:
            pass
        return None

    async def _get_website_url(self, page: Page) -> str | None:
        """公式サイトURLを取得"""
        try:
            website_button = await page.query_selector('[data-item-id="authority"]')
            if website_button:
                href = await website_button.get_attribute("href")
                if href:
                    return href

            website_link = await page.query_selector('a[data-value="ウェブサイト"]')
            if website_link:
                href = await website_link.get_attribute("href")
                if href:
                    return href
        except Exception:
            pass
        return None

    async def _get_phone(self, page: Page) -> str | None:
        """電話番号を取得"""
        try:
            phone_el = await page.query_selector('[data-item-id^="phone"]')
            if phone_el:
                text = await phone_el.inner_text()
                match = re.search(r"[\d\-]+", text)
                if match:
                    return match.group()
        except Exception:
            pass
        return None

    async def _get_rating(self, page: Page) -> float | None:
        """評価を取得"""
        try:
            rating_el = await page.query_selector('[role="img"][aria-label*="つ星"]')
            if rating_el:
                label = await rating_el.get_attribute("aria-label")
                if label:
                    match = re.search(r"([\d.]+)", label)
                    if match:
                        return float(match.group(1))
        except Exception:
            pass
        return None

    async def _get_reviews(self, page: Page) -> int | None:
        """口コミ数を取得"""
        try:
            reviews_el = await page.query_selector('[aria-label*="件のクチコミ"]')
            if reviews_el:
                label = await reviews_el.get_attribute("aria-label")
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

        match = re.search(r"([^\s]+区)", address)
        if match:
            return match.group(1)

        match = re.search(r"([^\s]+市)", address)
        if match:
            return match.group(1)

        return ""
