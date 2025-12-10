"""アプリケーション設定管理"""

import os
import logging
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from app.exceptions import ConfigurationError

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class Config:
    """アプリケーション設定クラス"""

    # 基本パス
    BASE_DIR = Path(__file__).parent.parent
    CONFIG_DIR = BASE_DIR / "config"

    # 環境変数（必須）
    REQUIRED_ENV_VARS = [
        "ANTHROPIC_API_KEY",
    ]

    # 環境変数（Sheets使用時に必須）
    SHEETS_REQUIRED_ENV_VARS = [
        "GOOGLE_SHEETS_CREDENTIALS",
        "GOOGLE_SHEETS_ID",
    ]

    def __init__(self) -> None:
        # .envファイル読み込み
        load_dotenv()

        # 設定ファイル読み込み
        self._default_config = self._load_yaml("default.yaml")
        self._exclusion_config = self._load_yaml("exclusion_keywords.yaml")

        # 環境変数検証
        self._validate_env_vars()

    def _load_yaml(self, filename: str) -> dict[str, Any]:
        """YAML設定ファイルを読み込む"""
        filepath = self.CONFIG_DIR / filename
        if not filepath.exists():
            logger.warning(f"Config file not found: {filepath}")
            return {}

        with open(filepath, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _validate_env_vars(self) -> None:
        """必須環境変数の存在確認"""
        missing = []
        for var in self.REQUIRED_ENV_VARS:
            if not os.environ.get(var):
                missing.append(var)

        if missing:
            logger.warning(
                f"Missing required environment variables: {missing}. "
                "Some features may not work properly."
            )

    def validate_sheets_config(self) -> None:
        """Google Sheets設定の検証（使用時に呼び出し）"""
        missing = []
        for var in self.SHEETS_REQUIRED_ENV_VARS:
            if not os.environ.get(var):
                missing.append(var)

        if missing:
            raise ConfigurationError(
                "Google Sheets configuration is incomplete",
                details={"missing_vars": missing},
            )

    # プロパティ: 環境変数
    @property
    def flask_env(self) -> str:
        return os.environ.get("FLASK_ENV", "production")

    @property
    def secret_key(self) -> str:
        return os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

    @property
    def anthropic_api_key(self) -> str:
        return os.environ.get("ANTHROPIC_API_KEY", "")

    @property
    def google_sheets_credentials(self) -> str:
        return os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")

    @property
    def google_sheets_id(self) -> str:
        return os.environ.get("GOOGLE_SHEETS_ID", "")

    @property
    def google_sheets_name(self) -> str:
        return os.environ.get(
            "GOOGLE_SHEETS_NAME",
            self._default_config.get("google_sheets", {}).get("sheet_name", "営業リスト"),
        )

    # プロパティ: YAML設定
    @property
    def project_name(self) -> str:
        return self._default_config.get("project_name", "AGA営業リスト")

    @property
    def search_suffix(self) -> str:
        return self._default_config.get("search_suffix", "AGA")

    @property
    def max_results_per_query(self) -> int:
        return self._default_config.get("scraping", {}).get("max_results_per_query", 50)

    @property
    def claude_model(self) -> str:
        return self._default_config.get("claude", {}).get(
            "model", "claude-sonnet-4-20250514"
        )

    @property
    def claude_batch_size(self) -> int:
        return self._default_config.get("claude", {}).get("batch_size", 10)

    @property
    def exclusion_keywords(self) -> list[str]:
        return self._exclusion_config.get("exclusion_keywords", [])

    @property
    def output_columns(self) -> list[str]:
        return self._default_config.get("output_columns", [])

    def update_exclusion_keywords(self, keywords: list[str]) -> None:
        """除外キーワードを更新してファイルに保存"""
        self._exclusion_config["exclusion_keywords"] = keywords
        filepath = self.CONFIG_DIR / "exclusion_keywords.yaml"
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(
                self._exclusion_config, f, allow_unicode=True, default_flow_style=False
            )

    def update_default_config(
        self,
        project_name: str | None = None,
        search_suffix: str | None = None,
        sheet_name: str | None = None,
    ) -> None:
        """デフォルト設定を更新してファイルに保存"""
        if project_name is not None:
            self._default_config["project_name"] = project_name
        if search_suffix is not None:
            self._default_config["search_suffix"] = search_suffix
        if sheet_name is not None:
            self._default_config.setdefault("google_sheets", {})["sheet_name"] = (
                sheet_name
            )

        filepath = self.CONFIG_DIR / "default.yaml"
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(
                self._default_config, f, allow_unicode=True, default_flow_style=False
            )


# グローバル設定インスタンス
config = Config()
