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

        # 既存データ取得
        existing_data = sheet.get_all_records()
        existing_urls: set[str] = {
            row.get("公式サイトURL", "") for row in existing_data if row.get("公式サイトURL")
        }
        existing_phones: set[str] = {
            row.get("電話番号", "") for row in existing_data if row.get("電話番号")
        }

        # 次のNo.を計算
        next_no = len(existing_data) + 1

        # 新規クリニックのみ抽出
        new_rows: list[list[Any]] = []
        for clinic in clinics:
            # URLまたは電話番号で重複チェック
            url = clinic.get("url", "")
            phone = clinic.get("phone", "")

            if url and url in existing_urls:
                logger.debug(f"Duplicate URL: {url}")
                continue
            if phone and phone in existing_phones:
                logger.debug(f"Duplicate phone: {phone}")
                continue

            row = [
                next_no,  # No.
                clinic.get("name", ""),  # クリニック名
                url,  # 公式サイトURL
                "",  # 問い合わせフォームURL/メール
                "",  # お知らせ/ブログURL
                clinic.get("area", ""),  # 所在地（区）
                phone,  # 電話番号
                clinic.get("rating", ""),  # 評価
                clinic.get("reviews", ""),  # 口コミ数
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

            # 追加したURLと電話を記録（同バッチ内の重複防止）
            if url:
                existing_urls.add(url)
            if phone:
                existing_phones.add(phone)

            next_no += 1

        # 一括追加
        if new_rows:
            sheet.append_rows(new_rows, value_input_option="USER_ENTERED")
            logger.info(f"Added {len(new_rows)} new rows to Google Sheets")

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
