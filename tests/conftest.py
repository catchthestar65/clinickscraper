"""pytest共通設定・fixtures"""

import os
import pytest
from unittest.mock import patch

# テスト用環境変数を設定
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-api-key")


@pytest.fixture
def app():
    """Flaskテストアプリケーション"""
    from app.main import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    """Flaskテストクライアント"""
    return app.test_client()


@pytest.fixture
def mock_config():
    """設定モック"""
    with patch("app.config.config") as mock:
        mock.exclusion_keywords = ["AGAスキンクリニック", "湘南美容"]
        mock.anthropic_api_key = "test-key"
        mock.claude_model = "claude-sonnet-4-20250514"
        mock.claude_batch_size = 10
        mock.google_sheets_id = "test-sheet-id"
        mock.google_sheets_name = "テストシート"
        mock.google_sheets_credentials = '{"type": "service_account"}'
        yield mock


@pytest.fixture
def sample_clinic_data():
    """サンプルクリニックデータ"""
    return {
        "name": "テストAGAクリニック",
        "url": "https://test-clinic.com",
        "address": "東京都新宿区西新宿1-1-1",
        "phone": "03-1234-5678",
        "rating": 4.5,
        "reviews": 100,
        "area": "新宿区",
    }


@pytest.fixture
def sample_clinics():
    """複数のサンプルクリニック"""
    from app.models.clinic import Clinic

    return [
        Clinic(
            name="テストAGAクリニック新宿院",
            url="https://test-clinic.com",
            address="東京都新宿区西新宿1-1-1",
            phone="03-1234-5678",
            rating=4.5,
            reviews=100,
            area="新宿区",
        ),
        Clinic(
            name="AGAスキンクリニック渋谷院",  # 除外対象
            url="https://aga-skin.com",
            address="東京都渋谷区渋谷1-1-1",
            phone="03-2345-6789",
            rating=4.0,
            reviews=200,
            area="渋谷区",
        ),
        Clinic(
            name="ローカルAGAクリニック",
            url="https://local-aga.com",
            address="東京都港区六本木1-1-1",
            phone="03-3456-7890",
            rating=4.8,
            reviews=50,
            area="港区",
        ),
    ]
