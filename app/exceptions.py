"""カスタム例外クラス定義"""

from typing import Any


class ClinicScraperError(Exception):
    """アプリケーション基底例外"""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(ClinicScraperError):
    """設定エラー（環境変数不足など）"""

    pass


class ScrapingError(ClinicScraperError):
    """スクレイピング処理エラー"""

    pass


class ValidationError(ClinicScraperError):
    """Claude API検証エラー"""

    pass


class SheetsError(ClinicScraperError):
    """Google Sheets操作エラー"""

    pass


class RateLimitError(ClinicScraperError):
    """API レート制限エラー"""

    pass
