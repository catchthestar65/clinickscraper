"""設定ルートのテスト"""

import pytest
from unittest.mock import patch, MagicMock


class TestSettingsRoutes:
    """設定APIエンドポイントのテスト"""

    def test_get_settings(self, client):
        """設定取得"""
        with patch("app.routes.settings.config") as mock_config:
            mock_config.project_name = "テストプロジェクト"
            mock_config.search_suffix = "AGA"
            mock_config.exclusion_keywords = ["キーワード1"]
            mock_config.google_sheets_id = "test-id"
            mock_config.google_sheets_name = "テストシート"

            response = client.get("/api/settings/")

            assert response.status_code == 200
            data = response.get_json()
            assert data["project_name"] == "テストプロジェクト"
            assert data["search_suffix"] == "AGA"
            assert "キーワード1" in data["exclusion_keywords"]

    def test_update_settings(self, client):
        """設定更新"""
        with patch("app.routes.settings.config") as mock_config:
            mock_config.update_default_config = MagicMock()
            mock_config.update_exclusion_keywords = MagicMock()

            response = client.post(
                "/api/settings/",
                json={
                    "project_name": "新しいプロジェクト",
                    "search_suffix": "美容",
                    "exclusion_keywords": ["新キーワード"],
                },
            )

            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True

            mock_config.update_default_config.assert_called_once()
            mock_config.update_exclusion_keywords.assert_called_once()

    def test_update_settings_no_data(self, client):
        """データなしでの設定更新"""
        response = client.post("/api/settings/", json=None)

        assert response.status_code == 400

    def test_get_exclusion_keywords(self, client):
        """除外キーワード取得"""
        with patch("app.routes.settings.config") as mock_config:
            mock_config.exclusion_keywords = ["キーワード1", "キーワード2"]

            response = client.get("/api/settings/exclusion-keywords")

            assert response.status_code == 200
            data = response.get_json()
            assert "keywords" in data
            assert len(data["keywords"]) == 2

    def test_add_exclusion_keyword(self, client):
        """除外キーワード追加"""
        with patch("app.routes.settings.ExclusionFilter") as MockFilter:
            mock_instance = MagicMock()
            mock_instance.keywords = ["既存", "新規"]
            MockFilter.return_value = mock_instance

            response = client.post(
                "/api/settings/exclusion-keywords",
                json={"keyword": "新規"},
            )

            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True
            mock_instance.add_keyword.assert_called_once_with("新規")
            mock_instance.save.assert_called_once()

    def test_add_exclusion_keyword_empty(self, client):
        """空のキーワード追加（エラー）"""
        response = client.post(
            "/api/settings/exclusion-keywords",
            json={"keyword": ""},
        )

        assert response.status_code == 400

    def test_remove_exclusion_keyword(self, client):
        """除外キーワード削除"""
        with patch("app.routes.settings.ExclusionFilter") as MockFilter:
            mock_instance = MagicMock()
            mock_instance.keywords = ["残す"]
            MockFilter.return_value = mock_instance

            response = client.delete(
                "/api/settings/exclusion-keywords",
                json={"keyword": "削除"},
            )

            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True
            mock_instance.remove_keyword.assert_called_once_with("削除")

    def test_test_sheets_connection(self, client):
        """Sheets接続テスト"""
        with patch("app.routes.settings.SheetsWriter") as MockWriter:
            mock_instance = MagicMock()
            mock_instance.test_connection.return_value = {
                "success": True,
                "spreadsheet_title": "テストシート",
            }
            MockWriter.return_value = mock_instance

            response = client.post("/api/settings/test-sheets")

            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True
