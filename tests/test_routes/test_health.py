"""ヘルスチェックルートのテスト"""

import pytest


class TestHealthRoutes:
    """ヘルスチェックエンドポイントのテスト"""

    def test_health_check(self, client):
        """ヘルスチェック正常応答"""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "healthy"

    def test_readiness_check(self, client):
        """レディネスチェック正常応答"""
        response = client.get("/ready")

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ready"
