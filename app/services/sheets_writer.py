"""Google Sheetsへの書き込みサービス"""

import json
import logging
from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from app.config import config
from app.exceptions import SheetsError, ConfigurationError

logger = logging.getLogger(__name__)


class SheetsWriter:
    """Google Sheetsへの書き込み"""

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    def __init__(self) -> None:
        self._client: gspread.Client | None = None
        self._spreadsheet_id = config.google_sheets_id
        self._sheet_name = config.google_sheets_name

    def _get_client(self) -> gspread.Client:
        """認証済みクライアントを取得"""
        if self._client is not None:
            return self._client

        creds_json = config.google_sheets_credentials
        if not creds_json:
            raise ConfigurationError(
                "GOOGLE_SHEETS_CREDENTIALS is not set",
                details={"hint": "Set the environment variable with service account JSON"},
            )

        try:
            creds_dict = json.loads(creds_json)
            credentials = Credentials.from_service_account_info(
                creds_dict, scopes=self.SCOPES
            )
            self._client = gspread.authorize(credentials)
            return self._client
        except json.JSONDecodeError as e:
            raise ConfigurationError(
                "Invalid GOOGLE_SHEETS_CREDENTIALS JSON",
                details={"error": str(e)},
            )
        except Exception as e:
            raise SheetsError(
                "Failed to authenticate with Google Sheets",
                details={"error": str(e)},
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(gspread.exceptions.APIError),
        reraise=True,
    )
    def append(self, clinics: list[dict]) -> int:
        """
        クリニック情報をシートに追記（重複除外）

        Args:
            clinics: クリニック情報のリスト

        Returns:
            新規追加された件数
        """
        if not self._spreadsheet_id:
            raise ConfigurationError(
                "GOOGLE_SHEETS_ID is not set",
                details={"hint": "Set the spreadsheet ID from the URL"},
            )

        client = self._get_client()
        spreadsheet = client.open_by_key(self._spreadsheet_id)

        try:
            sheet = spreadsheet.worksheet(self._sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            # シートがなければ作成
            sheet = spreadsheet.add_worksheet(
                title=self._sheet_name, rows=1000, cols=20
            )
            # ヘッダー行を追加
            headers = config.output_columns
            if headers:
                sheet.append_row(headers)
            logger.info(f"Created new worksheet: {self._sheet_name}")

        # 既存データ取得（空行を除く）
        all_values = sheet.get_all_values()

        # ヘッダー行をスキップして、実際のデータがある行を取得
        existing_urls: set[str] = set()
        last_data_row = 1  # ヘッダー行（デフォルト）
        data_count = 0  # 実際のデータ件数

        for i, row in enumerate(all_values[1:], start=2):  # 2行目から（1行目はヘッダー）
            # 有効なデータ行の判定:
            # - No.（1列目）が数値または空でない
            # - クリニック名（2列目）が空でない
            # - URL（3列目）が空でないまたはクリニック名が有効
            has_no = len(row) > 0 and row[0].strip()
            has_name = len(row) > 1 and row[1].strip()
            has_url = len(row) > 2 and row[2].strip()

            # クリニック名とURLの両方がある場合のみ有効データとみなす
            if has_name and has_url:
                last_data_row = i
                data_count += 1
                existing_urls.add(row[2].strip())
            # クリニック名だけある場合も有効データとみなす（URLがない場合）
            elif has_name and has_no:
                last_data_row = i
                data_count += 1

        logger.info(f"Found {data_count} existing data rows, last at row {last_data_row}")

        # 次のNo.を計算
        next_no = data_count + 1  # 1から始まる連番

        # 新規クリニックのみ抽出
        new_rows: list[list[Any]] = []
        for clinic in clinics:
            url = clinic.get("url", "")
            name = clinic.get("name", "")

            # URLのみで重複チェック（同じクリニック名でも別の院は許可）
            if url and url in existing_urls:
                logger.debug(f"Duplicate URL: {url}")
                continue

            # URLがない場合はスキップ（重複チェックできないため）
            if not url:
                logger.debug(f"No URL for clinic: {name}, skipping")
                continue

            # スプレッドシートのカラム構造に合わせる
            # No., クリニック名, 公式サイトURL, 問い合わせフォームURL, お知らせ/ブログURL,
            # 所在地（区）, ステータス, 初回送信日, フォロー1回目, フォロー2回目,
            # 合意日, 掲載日, リンク確認日, 備考
            row = [
                next_no,  # No.
                name,  # クリニック名
                url,  # 公式サイトURL
                "",  # 問い合わせフォームURL/メール
                "",  # お知らせ/ブログURL
                clinic.get("area", ""),  # 所在地（区）
                "未送信",  # ステータス
                "",  # 初回送信日
                "",  # フォロー1回目
                "",  # フォロー2回目
                "",  # 合意日
                "",  # 掲載日
                "",  # リンク確認日
                "",  # 備考
            ]
            new_rows.append(row)

            # 追加したURLを記録（同バッチ内の重複防止）
            existing_urls.add(url)

            next_no += 1

        # 一括追加（最後のデータ行の次に挿入）
        if new_rows:
            # 挿入位置を指定して追加
            start_row = last_data_row + 1
            sheet.update(f"A{start_row}", new_rows, value_input_option="USER_ENTERED")
            logger.info(f"Added {len(new_rows)} new rows to Google Sheets at row {start_row}")

        return len(new_rows)

    def test_connection(self) -> dict[str, Any]:
        """
        接続テスト

        Returns:
            テスト結果を含む辞書
        """
        try:
            if not self._spreadsheet_id:
                return {
                    "success": False,
                    "error": "GOOGLE_SHEETS_ID is not configured",
                }

            client = self._get_client()
            spreadsheet = client.open_by_key(self._spreadsheet_id)

            try:
                sheet = spreadsheet.worksheet(self._sheet_name)
                row_count = sheet.row_count
            except gspread.exceptions.WorksheetNotFound:
                row_count = 0

            return {
                "success": True,
                "spreadsheet_title": spreadsheet.title,
                "sheet_name": self._sheet_name,
                "row_count": row_count,
            }
        except ConfigurationError as e:
            return {
                "success": False,
                "error": e.message,
                "details": e.details,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }

    def get_existing_count(self) -> int:
        """既存レコード数を取得"""
        try:
            client = self._get_client()
            spreadsheet = client.open_by_key(self._spreadsheet_id)
            sheet = spreadsheet.worksheet(self._sheet_name)
            return len(sheet.get_all_records())
        except Exception:
            return 0
