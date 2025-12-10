"""除外フィルターのテスト"""

import pytest
from app.models.clinic import Clinic
from app.services.exclusion_filter import ExclusionFilter


class TestExclusionFilter:
    """ExclusionFilterのテスト"""

    def test_init_with_keywords(self):
        """キーワード指定での初期化"""
        keywords = ["キーワード1", "キーワード2"]
        filter_ = ExclusionFilter(keywords=keywords)

        assert filter_.keywords == keywords

    def test_should_exclude_exact_match(self):
        """完全一致の除外判定"""
        filter_ = ExclusionFilter(keywords=["AGAスキンクリニック"])

        assert filter_.should_exclude("AGAスキンクリニック") is True
        assert filter_.should_exclude("AGAスキンクリニック新宿院") is True
        assert filter_.should_exclude("テストクリニック") is False

    def test_should_exclude_case_insensitive(self):
        """大文字小文字を無視した除外判定"""
        filter_ = ExclusionFilter(keywords=["TCB"])

        assert filter_.should_exclude("TCB美容外科") is True
        assert filter_.should_exclude("tcb美容外科") is True
        assert filter_.should_exclude("Tcb美容外科") is True

    def test_filter_clinics(self, sample_clinics):
        """クリニックリストのフィルタリング"""
        filter_ = ExclusionFilter(keywords=["AGAスキンクリニック"])

        filtered = filter_.filter(sample_clinics)

        assert len(filtered) == 2
        assert all("AGAスキンクリニック" not in c.name for c in filtered)

    def test_filter_empty_list(self):
        """空リストのフィルタリング"""
        filter_ = ExclusionFilter(keywords=["test"])

        filtered = filter_.filter([])

        assert filtered == []

    def test_add_keyword(self):
        """キーワード追加"""
        filter_ = ExclusionFilter(keywords=["既存"])

        filter_.add_keyword("新規")

        assert "新規" in filter_.keywords
        assert len(filter_.keywords) == 2

    def test_add_keyword_duplicate(self):
        """重複キーワードの追加（追加されない）"""
        filter_ = ExclusionFilter(keywords=["既存"])

        filter_.add_keyword("既存")

        assert filter_.keywords.count("既存") == 1

    def test_add_keyword_empty(self):
        """空文字キーワードの追加（追加されない）"""
        filter_ = ExclusionFilter(keywords=["既存"])

        filter_.add_keyword("")
        filter_.add_keyword("   ")

        assert len(filter_.keywords) == 1

    def test_remove_keyword(self):
        """キーワード削除"""
        filter_ = ExclusionFilter(keywords=["削除対象", "残す"])

        filter_.remove_keyword("削除対象")

        assert "削除対象" not in filter_.keywords
        assert "残す" in filter_.keywords

    def test_remove_keyword_not_exists(self):
        """存在しないキーワードの削除（エラーなし）"""
        filter_ = ExclusionFilter(keywords=["既存"])

        filter_.remove_keyword("存在しない")

        assert filter_.keywords == ["既存"]

    def test_keywords_property_returns_copy(self):
        """keywordsプロパティがコピーを返すこと"""
        filter_ = ExclusionFilter(keywords=["キーワード"])

        keywords = filter_.keywords
        keywords.append("追加")

        assert "追加" not in filter_.keywords
