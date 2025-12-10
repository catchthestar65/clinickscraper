"""クリニックデータモデル（Pydantic）"""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Clinic(BaseModel):
    """クリニック基本情報"""

    name: str = Field(..., description="クリニック名")
    url: Optional[str] = Field(None, description="公式サイトURL")
    address: Optional[str] = Field(None, description="住所")
    phone: Optional[str] = Field(None, description="電話番号")
    rating: Optional[float] = Field(None, ge=0, le=5, description="評価（0-5）")
    reviews: Optional[int] = Field(None, ge=0, description="口コミ数")
    area: Optional[str] = Field(None, description="所在地（区）")

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("クリニック名は必須です")
        return v.strip()

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if v and not v.startswith(("http://", "https://")):
            return None
        return v

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        # 数字とハイフンのみ抽出
        import re

        normalized = re.sub(r"[^\d\-]", "", v)
        return normalized if normalized else None


class ClinicValidation(BaseModel):
    """Claude API検証結果"""

    index: int = Field(..., description="バッチ内のインデックス")
    is_official_site: Optional[bool] = Field(
        None, description="公式サイトかどうか（null=URL不明）"
    )
    is_major_chain: bool = Field(False, description="大手チェーンかどうか")
    normalized_name: str = Field(..., description="正規化されたクリニック名")
    reason: Optional[str] = Field(None, description="判定理由")


class ValidatedClinic(Clinic):
    """検証済みクリニック情報"""

    is_official_site: Optional[bool] = None
    is_major_chain: bool = False
    normalized_name: Optional[str] = None
    validation_reason: Optional[str] = None
    is_valid: bool = True  # 最終的な有効判定

    def compute_validity(self) -> None:
        """有効性を計算（公式サイトかつ大手チェーンでない）"""
        self.is_valid = (
            self.is_official_site is not False and self.is_major_chain is False
        )


class ScrapeRequest(BaseModel):
    """スクレイピングリクエスト"""

    regions: list[str] = Field(..., min_length=1, description="検索地域リスト")
    search_suffix: str = Field("AGA", description="検索サフィックス")

    @field_validator("regions")
    @classmethod
    def validate_regions(cls, v: list[str]) -> list[str]:
        # 空文字を除去し、トリム
        cleaned = [r.strip() for r in v if r and r.strip()]
        if not cleaned:
            raise ValueError("少なくとも1つの地域を指定してください")
        return cleaned


class ScrapeResponse(BaseModel):
    """スクレイピング結果"""

    success: bool
    total_found: int = 0
    valid_count: int = 0
    excluded_count: int = 0
    new_count: int = 0
    clinics: list[dict] = Field(default_factory=list)
    error: Optional[str] = None
