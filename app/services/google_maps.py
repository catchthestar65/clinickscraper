"""Google Mapsスクレイピングサービス"""

import logging
import os
import re
import signal
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from contextlib import contextmanager
from typing import Generator, Any

from playwright.sync_api import sync_playwright, Browser, Page, Playwright, TimeoutError as PlaywrightTimeout
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import config
from app.exceptions import ScrapingError
from app.models.clinic import Clinic

logger = logging.getLogger(__name__)


def _cleanup_with_timeout(func, timeout_seconds: int = 5, description: str = ""):
    """
    タイムアウト付きでクリーンアップ関数を実行（スレッドセーフ版）

    Args:
        func: 実行する関数
        timeout_seconds: タイムアウト秒数
        description: ログ用の説明

    Returns:
        成功したかどうか
    """
    import threading

    result = {"success": False, "error": None}

    def run_cleanup():
        try:
            func()
            result["success"] = True
        except Exception as e:
            result["error"] = e

    # 別スレッドでクリーンアップを実行し、タイムアウトを設定
    cleanup_thread = threading.Thread(target=run_cleanup)
    cleanup_thread.daemon = True  # メインスレッド終了時に強制終了
    cleanup_thread.start()
    cleanup_thread.join(timeout=timeout_seconds)

    if cleanup_thread.is_alive():
        # タイムアウト - スレッドはデーモンなので放置してOK
        logger.warning(f"[BROWSER] {description}がタイムアウト ({timeout_seconds}秒) - スキップして続行")
        return False

    if result["error"]:
        logger.warning(f"[BROWSER] {description}エラー: {result['error']}")
        return False

    return result["success"]

# グローバルスレッドプール（Playwrightをasyncioループ外で実行）
_executor = ThreadPoolExecutor(max_workers=2)


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
        _log_memory("ブラウザ起動前")
        logger.info("[BROWSER] Playwright開始...")

        playwright = None
        browser = None
        context = None

        try:
            playwright = sync_playwright().start()
            logger.info("[BROWSER] Chromium起動中...")

            # メモリ使用量を削減するブラウザ引数
            browser = playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-dev-shm-usage",  # /dev/shm使用を無効化（メモリ節約）
                    "--disable-gpu",  # GPU無効化
                    "--no-sandbox",  # サンドボックス無効化
                    "--single-process",  # シングルプロセスモード
                    "--disable-setuid-sandbox",
                    "--disable-extensions",  # 拡張機能無効化
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--disable-translate",
                    "--no-first-run",
                    "--disable-features=site-per-process",  # プロセス分離無効化
                    "--js-flags=--max-old-space-size=256",  # JSヒープ制限
                ],
            )

            _log_memory("Chromium起動後")
            logger.info("[BROWSER] コンテキスト作成中...")

            context = browser.new_context(
                locale="ja-JP",
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
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

            # タイムアウト付きクリーンアップ（各ステップ最大5秒）
            # ハングを防ぐため、タイムアウト時は強制的に次へ進む
            CLEANUP_TIMEOUT = 5  # 秒

            if context:
                success = _cleanup_with_timeout(
                    lambda: context.close(),
                    timeout_seconds=CLEANUP_TIMEOUT,
                    description="コンテキスト終了"
                )
                if success:
                    logger.info("[BROWSER] コンテキスト終了完了")

            if browser:
                success = _cleanup_with_timeout(
                    lambda: browser.close(),
                    timeout_seconds=CLEANUP_TIMEOUT,
                    description="ブラウザ終了"
                )
                if success:
                    logger.info("[BROWSER] ブラウザ終了完了")

            if playwright:
                success = _cleanup_with_timeout(
                    lambda: playwright.stop(),
                    timeout_seconds=CLEANUP_TIMEOUT,
                    description="Playwright停止"
                )
                if success:
                    logger.info("[BROWSER] Playwright停止完了")

            cleanup_elapsed = time.time() - cleanup_start
            logger.info(f"[BROWSER] クリーンアップ完了 ({cleanup_elapsed:.1f}秒)")
            _log_memory("ブラウザ終了後")

    def search(self, query: str, max_results: int | None = None) -> list[Clinic]:
        """
        検索クエリでGoogle Mapsを検索し、クリニック情報を取得
        （ThreadPoolExecutor経由でasyncioループ外で実行）

        Args:
            query: 検索クエリ（例: "新宿 AGA"）
            max_results: 最大取得件数（指定なしでデフォルト値使用）

        Returns:
            クリニック情報のリスト
        """
        # ThreadPoolExecutorで別スレッドで実行（asyncioループとの競合を回避）
        future = _executor.submit(self._search_impl, query, max_results)
        return future.result()

    def _search_impl(self, query: str, max_results: int | None = None) -> list[Clinic]:
        """検索の実装（別スレッドで実行）"""
        max_results = max_results or self.max_results
        clinics: list[Clinic] = []
        start_time = time.time()

        logger.info(f"[SEARCH] 検索開始: '{query}' (最大: {max_results}件)")
        _log_memory("検索開始")

        with self._browser_context() as page:
            try:
                # Google Maps検索
                search_url = f"{self.BASE_URL}{query.replace(' ', '+')}"
                logger.info(f"[SEARCH] URL: {search_url}")
                logger.info("[SEARCH] ページ読み込み中...")

                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
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
                page.wait_for_timeout(3000)
                _log_memory("ページ読み込み後")

                # クッキー同意ダイアログがあれば閉じる
                self._handle_consent_dialog(page)

                # 単一結果ページかどうかをチェック
                feed = page.query_selector('[role="feed"]')
                h1_el = page.query_selector("h1")
                h1_text = h1_el.inner_text() if h1_el else ""
                logger.info(f"[SEARCH] ページ解析: feed={feed is not None}, h1='{h1_text}'")

                # 単一結果ページの場合、クエリを修正してリトライ
                if not feed and h1_text and h1_text != "結果":
                    logger.warning(f"[SEARCH] 単一結果ページ検出: {h1_text}")

                    # クエリに「クリニック」を追加してリトライ
                    # （既に含まれている場合や明らかに異なる検索の場合はスキップ）
                    retry_keywords = ["クリニック", "病院", "医院"]
                    should_retry = not any(kw in query for kw in retry_keywords)

                    if should_retry:
                        modified_query = f"{query} クリニック"
                        logger.info(f"[SEARCH] クエリを修正してリトライ: '{modified_query}'")

                        retry_url = f"{self.BASE_URL}{modified_query.replace(' ', '+')}"
                        try:
                            page.goto(retry_url, wait_until="domcontentloaded", timeout=60000)
                            page.wait_for_timeout(3000)
                            self._handle_consent_dialog(page)

                            # 再度ページ構造を確認
                            feed = page.query_selector('[role="feed"]')
                            h1_el = page.query_selector("h1")
                            h1_text = h1_el.inner_text() if h1_el else ""
                            logger.info(f"[SEARCH] リトライ後ページ解析: feed={feed is not None}, h1='{h1_text}'")
                        except Exception as e:
                            logger.warning(f"[SEARCH] リトライ失敗: {e}")

                # 最終的なページタイプに基づいて処理
                if not feed and h1_text and h1_text != "結果":
                    # それでも単一結果ページの場合は1件だけ抽出
                    logger.info(f"[SEARCH] 単一結果として抽出: {h1_text}")
                    clinic = self._extract_single_result(page, h1_text)
                    if clinic:
                        clinics.append(clinic)
                        logger.info(f"[SEARCH] 単一結果を抽出: {clinic.name}")
                else:
                    # 複数結果ページ
                    logger.info("[SEARCH] 複数結果ページ - スクロール開始...")
                    self._scroll_results(page, max_results)
                    _log_memory("スクロール後")

                    results = page.query_selector_all('a[href*="/maps/place/"]')
                    logger.info(f"[SEARCH] 検索結果: {len(results)}件発見")

                    for i, result in enumerate(results[:max_results]):
                        try:
                            if i > 0 and i % 10 == 0:
                                _log_memory(f"抽出中 ({i}件目)")

                            clinic = self._extract_clinic_info(result, page, i)
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

    def _extract_single_result(self, page: Page, name: str) -> Clinic | None:
        """
        単一結果ページからクリニック情報を抽出

        Args:
            page: Playwrightページ
            name: h1から取得したクリニック名

        Returns:
            Clinicオブジェクト、抽出失敗時はNone
        """
        logger.debug(f"Extracting single result: {name}")

        # 公式サイトURL（単一結果ページでは異なるセレクタの可能性）
        url = self._get_website_url(page)

        # 代替: リンクテキストから取得
        if not url:
            website_link = page.query_selector('a[data-item-id="authority"]')
            if website_link:
                url = website_link.get_attribute("href")

        # 住所
        address = self._get_text(page, '[data-item-id="address"] .fontBodyMedium')
        # 代替セレクタ
        if not address:
            address_el = page.query_selector('button[data-item-id="address"]')
            if address_el:
                address = address_el.inner_text()

        # 電話番号
        phone = self._get_phone(page)

        # 評価
        rating = self._get_rating(page)

        # 口コミ数
        reviews = self._get_reviews(page)

        # 所在地（区）を抽出
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
            results = page.query_selector_all('a[href*="/maps/place/"]')
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
        extract_start = time.time()

        # aria-label属性からクリニック名を取得
        name = element.get_attribute("aria-label")
        if not name:
            logger.debug(f"[EXTRACT][{index}] aria-labelなし、スキップ")
            return None

        logger.info(f"[EXTRACT][{index}] 開始: '{name}'")

        # クリック前のh1を取得（パネル更新検出用）
        # 注: Google Mapsには複数のh1があり、クリニック名はh1.DUwDvfクラスに表示される
        prev_h1 = self._get_text(page, "h1.DUwDvf")
        logger.debug(f"[EXTRACT][{index}] クリック前 h1.DUwDvf='{prev_h1}'")

        # クリックして詳細パネルを開く（複数の方法を試行）
        click_success = self._click_element_robust(element, page, index, name)
        if not click_success:
            return None

        # パネル更新待機: h1.DUwDvfがクリックしたクリニック名に変わるまで待つ
        # これにより、URLと名前の不一致を防ぐ
        max_wait_attempts = 15  # 最大15回 × 0.2秒 = 3秒（高速化）
        panel_ready = False

        for attempt in range(max_wait_attempts):
            current_h1 = self._get_text(page, "h1.DUwDvf")

            # h1がクリックしたクリニック名と一致したらパネル更新完了
            if current_h1 and self._names_match(current_h1, name):
                logger.debug(f"[EXTRACT][{index}] パネル更新確認 (attempt={attempt+1}): h1='{current_h1}'")
                panel_ready = True
                break

            # h1が前と変わっていれば更新中
            if current_h1 and current_h1 != prev_h1:
                # 名前が完全一致しないが変わった場合も確認
                logger.debug(f"[EXTRACT][{index}] h1変更検出 (attempt={attempt+1}): '{prev_h1}' -> '{current_h1}'")
                # 追加で少し待って再確認
                time.sleep(0.2)
                final_h1 = self._get_text(page, "h1.DUwDvf")
                if final_h1 and self._names_match(final_h1, name):
                    logger.debug(f"[EXTRACT][{index}] 最終確認OK: h1='{final_h1}'")
                    panel_ready = True
                    break

            time.sleep(0.2)

        if not panel_ready:
            # タイムアウト - 再クリック試行
            final_h1 = self._get_text(page, "h1.DUwDvf")
            logger.warning(f"[EXTRACT][{index}] パネル更新タイムアウト: 期待='{name}', 実際h1='{final_h1}'")

            # h1が空なら再クリック試行
            if not final_h1:
                logger.info(f"[EXTRACT][{index}] JavaScriptクリックで再試行...")
                try:
                    element.evaluate("el => el.click()")
                    time.sleep(1.0)
                    final_h1 = self._get_text(page, "h1.DUwDvf")
                    if final_h1 and self._names_match(final_h1, name):
                        logger.info(f"[EXTRACT][{index}] 再クリック成功: h1='{final_h1}'")
                        panel_ready = True
                except Exception as e:
                    logger.debug(f"[EXTRACT][{index}] 再クリック失敗: {e}")

            # 名前が全く違う場合はスキップ（データ不整合を防ぐ）
            if not panel_ready:
                final_h1 = self._get_text(page, "h1.DUwDvf")
                if final_h1 and not self._names_match(final_h1, name):
                    logger.warning(f"[EXTRACT][{index}] 名前不一致のためスキップ: h1='{final_h1}'")
                    return None
                # それ以外は追加待機して続行（部分データでも収集）
                logger.info(f"[EXTRACT][{index}] パネル未確認だがデータ収集を試行")
                time.sleep(0.5)

        # データ取得前の追加安定化待機
        time.sleep(0.3)

        # 公式サイトURL取得
        url = self._get_website_url(page)
        logger.debug(f"[EXTRACT][{index}] URL取得: {url}")

        # 住所取得
        address = self._get_text(page, '[data-item-id="address"] .fontBodyMedium')
        logger.debug(f"[EXTRACT][{index}] 住所取得: {address}")

        # 電話番号取得
        phone = self._get_phone(page)
        logger.debug(f"[EXTRACT][{index}] 電話番号取得: {phone}")

        # 評価取得
        rating = self._get_rating(page)

        # 口コミ数取得
        reviews = self._get_reviews(page)

        # 所在地（区）を抽出
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

    def _click_element_robust(
        self, element: Any, page: Page, index: int, name: str
    ) -> bool:
        """
        要素を確実にクリックする（複数の方法を試行）

        Args:
            element: クリック対象の要素
            page: Playwrightページ
            index: 結果インデックス
            name: クリニック名（ログ用）

        Returns:
            クリック成功したかどうか
        """
        # 方法1: 要素をビューポートにスクロールしてからクリック
        try:
            element.scroll_into_view_if_needed()
            time.sleep(0.2)
            logger.debug(f"[EXTRACT][{index}] スクロール完了")
        except Exception as e:
            logger.debug(f"[EXTRACT][{index}] スクロール失敗: {e}")

        # 方法2: 通常のクリック
        try:
            element.click(timeout=5000)
            logger.debug(f"[EXTRACT][{index}] 通常クリック成功")
            return True
        except Exception as e:
            logger.debug(f"[EXTRACT][{index}] 通常クリック失敗: {type(e).__name__}: {e}")

        # 方法3: force=Trueでクリック（アクション可能性チェックをスキップ）
        try:
            element.click(force=True, timeout=5000)
            logger.debug(f"[EXTRACT][{index}] 強制クリック成功")
            return True
        except Exception as e:
            logger.debug(f"[EXTRACT][{index}] 強制クリック失敗: {type(e).__name__}: {e}")

        # 方法4: JavaScriptでクリック
        try:
            element.evaluate("el => el.click()")
            logger.debug(f"[EXTRACT][{index}] JSクリック成功")
            return True
        except Exception as e:
            logger.debug(f"[EXTRACT][{index}] JSクリック失敗: {type(e).__name__}: {e}")

        # 方法5: dispatchEventでクリックイベントを発火
        try:
            element.evaluate("""el => {
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

        # 方法6: 要素の中央座標を取得してpage.click
        try:
            box = element.bounding_box()
            if box:
                x = box["x"] + box["width"] / 2
                y = box["y"] + box["height"] / 2
                page.mouse.click(x, y)
                logger.debug(f"[EXTRACT][{index}] 座標クリック成功: ({x}, {y})")
                return True
        except Exception as e:
            logger.debug(f"[EXTRACT][{index}] 座標クリック失敗: {type(e).__name__}: {e}")

        logger.warning(f"[EXTRACT][{index}] 全クリック方法失敗: '{name}'")
        return False

    def _names_match(self, h1_name: str, aria_label_name: str) -> bool:
        """
        h1のクリニック名とaria-labelの名前が一致するか判定

        Google Mapsではh1とaria-labelで微妙に表記が異なることがあるため、
        部分一致や正規化して比較する
        """
        if not h1_name or not aria_label_name:
            return False

        # 完全一致
        if h1_name == aria_label_name:
            return True

        # 空白・全角半角の正規化
        h1_normalized = h1_name.replace(" ", "").replace("　", "").lower()
        aria_normalized = aria_label_name.replace(" ", "").replace("　", "").lower()

        # 正規化後の完全一致
        if h1_normalized == aria_normalized:
            return True

        # 部分一致（h1がaria-labelに含まれる、またはその逆）
        if h1_normalized in aria_normalized or aria_normalized in h1_normalized:
            return True

        # 先頭N文字が一致（長い名前の場合のトリミング対策）
        min_len = min(len(h1_normalized), len(aria_normalized))
        if min_len >= 5 and h1_normalized[:min_len] == aria_normalized[:min_len]:
            return True

        return False

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
