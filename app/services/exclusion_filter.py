"""除外キーワードによるフィルタリング"""

import logging
from typing import TYPE_CHECKING

from app.config import config

if TYPE_CHECKING:
    from app.models.clinic import Clinic

logger = logging.getLogger(__name__)


class ExclusionFilter:
    """除外キーワードフィルター"""

    def __init__(self, keywords: list[str] | None = None) -> None:
        """
        Args:
            keywords: 除外キーワードリスト（指定なしで設定ファイルから読み込み）
        """
        self._keywords = keywords if keywords is not None else config.exclusion_keywords

    @property
    def keywords(self) -> list[str]:
        """除外キーワード一覧"""
        return self._keywords.copy()

    def filter(self, clinics: list["Clinic"]) -> list["Clinic"]:
        """
        除外キーワードに該当するクリニックを除外

        Args:
            clinics: クリニック情報のリスト

        Returns:
            フィルタリング後のリスト
        """
        filtered = []
        excluded_count = 0

        for clinic in clinics:
            if self.should_exclude(clinic.name):
                excluded_count += 1
                logger.debug(f"Excluded: {clinic.name}")
            else:
                filtered.append(clinic)

        logger.info(f"Filtered {excluded_count} clinics by exclusion keywords")
        return filtered

    def should_exclude(self, name: str) -> bool:
        """
        クリニック名が除外対象かどうかを判定

        Args:
            name: クリニック名

        Returns:
            除外すべき場合True
        """
        name_lower = name.lower()
        for keyword in self._keywords:
            if keyword.lower() in name_lower:
                return True
        return False

    def add_keyword(self, keyword: str) -> None:
        """除外キーワードを追加"""
        keyword = keyword.strip()
        if keyword and keyword not in self._keywords:
            self._keywords.append(keyword)
            logger.info(f"Added exclusion keyword: {keyword}")

    def remove_keyword(self, keyword: str) -> None:
        """除外キーワードを削除"""
        if keyword in self._keywords:
            self._keywords.remove(keyword)
            logger.info(f"Removed exclusion keyword: {keyword}")

    def save(self) -> None:
        """現在のキーワードを設定ファイルに保存"""
        config.update_exclusion_keywords(self._keywords)
        logger.info("Saved exclusion keywords to config file")
