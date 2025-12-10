"""Sheets Writerのテスト"""

import pytest
from unittest.mock import patch, MagicMock

from app.services.sheets_writer import SheetsWriter
from app.exceptions import ConfigurationError


class TestSheetsWriter:
    """SheetsWriterのテスト"""

    @pytest.fixture
    def mock_config(self):
        """設定モック"""
        with patch("app.services.sheets_writer.config") as mock:
            mock.google_sheets_credentials = '{"type": "service_account", "project_id": "test"}'
            mock.google_sheets_id = "test-sheet-id"
            mock.google_sheets_name = "テストシート"
            mock.output_columns = ["No.", "クリニック名", "URL"]
            yield mock

    @pytest.fixture
    def writer(self, mock_config):
        """テスト用ライター"""
        return SheetsWriter()

    def test_test_connection_no_credentials(self):
        """認証情報なしでの接続テスト"""
        with patch("app.services.sheets_writer.config") as mock:
            mock.google_sheets_credentials = ""
            mock.google_sheets_id = "test-id"
            mock.google_sheets_name = "テスト"

            writer = SheetsWriter()
            result = writer.test_connection()

            assert result["success"] is False
            assert "GOOGLE_SHEETS_CREDENTIALS" in result["error"]

    def test_test_connection_no_sheet_id(self):
        """シートIDなしでの接続テスト"""
        with patch("app.services.sheets_writer.config") as mock:
            mock.google_sheets_credentials = '{"type": "service_account"}'
            mock.google_sheets_id = ""
            mock.google_sheets_name = "テスト"

            writer = SheetsWriter()
            result = writer.test_connection()

            assert result["success"] is False
            assert "GOOGLE_SHEETS_ID" in result["error"]

    def test_append_no_sheet_id(self, mock_config):
        """シートIDなしでの追記"""
        mock_config.google_sheets_id = ""

        writer = SheetsWriter()

        with pytest.raises(ConfigurationError) as exc_info:
            writer.append([{"name": "test"}])

        assert "GOOGLE_SHEETS_ID" in str(exc_info.value)

    def test_append_with_mock(self, writer, mock_config):
        """モックでの追記テスト"""
        mock_client = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_sheet = MagicMock()

        # 既存データなし
        mock_sheet.get_all_records.return_value = []
        mock_sheet.row_count = 1

        mock_spreadsheet.worksheet.return_value = mock_sheet
        mock_client.open_by_key.return_value = mock_spreadsheet

        with patch.object(writer, "_get_client", return_value=mock_client):
            clinics = [
                {
                    "name": "テストクリニック",
                    "url": "https://test.com",
                    "area": "新宿区",
                    "phone": "03-1234-5678",
                    "rating": 4.5,
                    "reviews": 100,
                }
            ]

            count = writer.append(clinics)

            assert count == 1
            mock_sheet.append_rows.assert_called_once()

    def test_append_duplicate_url(self, writer, mock_config):
        """URL重複時の追記"""
        mock_client = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_sheet = MagicMock()

        # 既存データにURLあり
        mock_sheet.get_all_records.return_value = [
            {"公式サイトURL": "https://existing.com", "電話番号": "03-0000-0000"}
        ]
        mock_sheet.row_count = 2

        mock_spreadsheet.worksheet.return_value = mock_sheet
        mock_client.open_by_key.return_value = mock_spreadsheet

        with patch.object(writer, "_get_client", return_value=mock_client):
            clinics = [
                {
                    "name": "既存クリニック",
                    "url": "https://existing.com",  # 重複URL
                    "area": "渋谷区",
                },
                {
                    "name": "新規クリニック",
                    "url": "https://new.com",
                    "area": "港区",
                },
            ]

            count = writer.append(clinics)

            assert count == 1  # 重複を除いた1件のみ追加

    def test_append_duplicate_phone(self, writer, mock_config):
        """電話番号重複時の追記"""
        mock_client = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_sheet = MagicMock()

        mock_sheet.get_all_records.return_value = [
            {"公式サイトURL": "", "電話番号": "03-1234-5678"}
        ]
        mock_sheet.row_count = 2

        mock_spreadsheet.worksheet.return_value = mock_sheet
        mock_client.open_by_key.return_value = mock_spreadsheet

        with patch.object(writer, "_get_client", return_value=mock_client):
            clinics = [
                {
                    "name": "テストクリニック",
                    "url": "https://test.com",
                    "phone": "03-1234-5678",  # 重複電話番号
                    "area": "新宿区",
                }
            ]

            count = writer.append(clinics)

            assert count == 0  # 重複のため追加なし

    def test_get_existing_count(self, writer, mock_config):
        """既存レコード数取得"""
        mock_client = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_sheet = MagicMock()

        mock_sheet.get_all_records.return_value = [
            {"name": "clinic1"},
            {"name": "clinic2"},
            {"name": "clinic3"},
        ]

        mock_spreadsheet.worksheet.return_value = mock_sheet
        mock_client.open_by_key.return_value = mock_spreadsheet

        with patch.object(writer, "_get_client", return_value=mock_client):
            count = writer.get_existing_count()

            assert count == 3
