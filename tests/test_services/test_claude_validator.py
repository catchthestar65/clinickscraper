"""Claude Validatorのテスト"""

import json
import pytest
from unittest.mock import patch, MagicMock

from app.models.clinic import Clinic
from app.services.claude_validator import ClaudeValidator


class TestClaudeValidator:
    """ClaudeValidatorのテスト"""

    @pytest.fixture
    def validator(self):
        """テスト用バリデーター"""
        with patch("app.services.claude_validator.config") as mock_config:
            mock_config.anthropic_api_key = "test-key"
            mock_config.claude_model = "claude-sonnet-4-20250514"
            mock_config.claude_batch_size = 10
            return ClaudeValidator()

    @pytest.fixture
    def sample_clinic(self):
        """サンプルクリニック"""
        return Clinic(
            name="テストAGAクリニック",
            url="https://test-clinic.com",
            address="東京都新宿区",
            phone="03-1234-5678",
            rating=4.5,
            reviews=100,
            area="新宿区",
        )

    def test_validate_batch_no_client(self):
        """APIクライアントなしでの検証"""
        with patch("app.services.claude_validator.config") as mock_config:
            mock_config.anthropic_api_key = ""  # 空のAPIキー

            validator = ClaudeValidator()
            clinics = [
                Clinic(name="テスト", url="https://test.com", address="東京", area="新宿区")
            ]

            results = validator.validate_batch(clinics)

            assert len(results) == 1
            assert results[0]["is_valid"] is True
            assert "API未設定" in results[0]["validation_reason"]

    def test_validate_batch_with_mock_api(self, validator, sample_clinic):
        """モックAPIでの検証"""
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=json.dumps(
                    [
                        {
                            "index": 0,
                            "is_official_site": True,
                            "is_major_chain": False,
                            "normalized_name": "テストAGAクリニック",
                            "reason": "公式サイト",
                        }
                    ]
                )
            )
        ]

        with patch.object(
            validator.client.messages, "create", return_value=mock_response
        ):
            results = validator.validate_batch([sample_clinic])

            assert len(results) == 1
            assert results[0]["is_valid"] is True
            assert results[0]["is_official_site"] is True
            assert results[0]["is_major_chain"] is False

    def test_validate_batch_major_chain(self, validator):
        """大手チェーン判定"""
        clinic = Clinic(
            name="AGAスキンクリニック新宿院",
            url="https://aga-skin.com",
            address="東京都新宿区",
            area="新宿区",
        )

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=json.dumps(
                    [
                        {
                            "index": 0,
                            "is_official_site": True,
                            "is_major_chain": True,
                            "normalized_name": "AGAスキンクリニック",
                            "reason": "全国展開の大手チェーン",
                        }
                    ]
                )
            )
        ]

        with patch.object(
            validator.client.messages, "create", return_value=mock_response
        ):
            results = validator.validate_batch([clinic])

            assert len(results) == 1
            assert results[0]["is_valid"] is False  # 大手チェーンは無効
            assert results[0]["is_major_chain"] is True

    def test_validate_batch_portal_site(self, validator):
        """ポータルサイト判定"""
        clinic = Clinic(
            name="テストクリニック",
            url="https://epark.jp/clinic/test",
            address="東京都渋谷区",
            area="渋谷区",
        )

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=json.dumps(
                    [
                        {
                            "index": 0,
                            "is_official_site": False,
                            "is_major_chain": False,
                            "normalized_name": "テストクリニック",
                            "reason": "EPARKポータルサイト",
                        }
                    ]
                )
            )
        ]

        with patch.object(
            validator.client.messages, "create", return_value=mock_response
        ):
            results = validator.validate_batch([clinic])

            assert len(results) == 1
            assert results[0]["is_valid"] is False  # ポータルサイトは無効

    def test_validate_batch_api_error(self, validator, sample_clinic):
        """APIエラー時のフォールバック"""
        with patch.object(
            validator.client.messages,
            "create",
            side_effect=Exception("API Error"),
        ):
            results = validator.validate_batch([sample_clinic])

            # エラー時も結果を返す（手動確認用）
            assert len(results) == 1
            assert results[0]["is_valid"] is True
            assert "API error" in results[0]["validation_reason"]

    def test_validate_batch_json_in_code_block(self, validator, sample_clinic):
        """コードブロック内のJSONパース"""
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='```json\n[{"index": 0, "is_official_site": true, "is_major_chain": false, "normalized_name": "test", "reason": "ok"}]\n```'
            )
        ]

        with patch.object(
            validator.client.messages, "create", return_value=mock_response
        ):
            results = validator.validate_batch([sample_clinic])

            assert len(results) == 1
            assert results[0]["is_valid"] is True

    def test_validate_single(self, validator, sample_clinic):
        """単一クリニック検証"""
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=json.dumps(
                    [
                        {
                            "index": 0,
                            "is_official_site": True,
                            "is_major_chain": False,
                            "normalized_name": "テストAGAクリニック",
                            "reason": "公式サイト",
                        }
                    ]
                )
            )
        ]

        with patch.object(
            validator.client.messages, "create", return_value=mock_response
        ):
            result = validator.validate_single(sample_clinic)

            assert result["is_valid"] is True
