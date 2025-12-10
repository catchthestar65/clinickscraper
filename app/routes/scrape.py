"""スクレイピングAPI"""

import json
import logging
from typing import Generator

from flask import Blueprint, request, jsonify, Response
from flask.typing import ResponseReturnValue
from pydantic import ValidationError as PydanticValidationError

from app.models.clinic import ScrapeRequest
from app.services.google_maps import GoogleMapsScraper
from app.services.exclusion_filter import ExclusionFilter
from app.services.claude_validator import ClaudeValidator
from app.services.sheets_writer import SheetsWriter
from app.exceptions import ScrapingError, SheetsError

logger = logging.getLogger(__name__)
bp = Blueprint("scrape", __name__, url_prefix="/api")


def _create_sse_message(type_: str, **data) -> str:
    """SSEメッセージを作成"""
    message = {"type": type_, **data}
    return f"data: {json.dumps(message, ensure_ascii=False)}\n\n"


@bp.route("/scrape", methods=["POST"])
def scrape() -> ResponseReturnValue:
    """
    スクレイピング実行API（SSE）

    リクエスト:
    {
        "regions": ["新宿", "渋谷", "池袋"],
        "search_suffix": "AGA"
    }

    レスポンス: Server-Sent Events (SSE) でリアルタイムログ送信
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    # リクエスト検証
    try:
        scrape_request = ScrapeRequest(**data)
    except PydanticValidationError as e:
        return jsonify({"success": False, "error": str(e)}), 400

    def generate() -> Generator[str, None, None]:
        try:
            scraper = GoogleMapsScraper()
            exclusion_filter = ExclusionFilter()
            validator = ClaudeValidator()
            sheets_writer = SheetsWriter()

            all_valid_clinics = []
            total_found = 0
            total_excluded = 0
            total_new = 0

            # 各地域を検索（地域ごとに即時Sheets書き込み）
            for i, region in enumerate(scrape_request.regions):
                query = f"{region} {scrape_request.search_suffix}"
                yield _create_sse_message(
                    "log",
                    message=f"[{i+1}/{len(scrape_request.regions)}] {query} で検索開始...",
                )

                try:
                    clinics = scraper.search(query)
                    yield _create_sse_message(
                        "log", message=f"{len(clinics)}件取得"
                    )

                    if not clinics:
                        yield _create_sse_message(
                            "log", message="→ スキップ（0件）"
                        )
                        continue

                    total_found += len(clinics)

                    # キーワード除外
                    filtered = exclusion_filter.filter(clinics)
                    excluded_count = len(clinics) - len(filtered)
                    total_excluded += excluded_count

                    if excluded_count > 0:
                        yield _create_sse_message(
                            "log", message=f"キーワード除外: {excluded_count}件"
                        )

                    if not filtered:
                        yield _create_sse_message(
                            "log", message="→ スキップ（有効0件）"
                        )
                        continue

                    # Claude API検証（地域ごと）
                    yield _create_sse_message(
                        "log", message=f"Claude APIで検証中..."
                    )
                    validated = validator.validate_batch(filtered)
                    valid_clinics = [c for c in validated if c.get("is_valid", False)]

                    yield _create_sse_message(
                        "log", message=f"有効クリニック: {len(valid_clinics)}件"
                    )

                    if not valid_clinics:
                        yield _create_sse_message(
                            "log", message="→ スキップ（検証後0件）"
                        )
                        continue

                    # Google Sheets書き込み（地域ごとに即時保存）
                    try:
                        new_count = sheets_writer.append(valid_clinics)
                        total_new += new_count
                        yield _create_sse_message(
                            "log",
                            message=f"→ Sheets保存: {new_count}件（累計: {total_new}件）",
                        )
                        all_valid_clinics.extend(valid_clinics)
                    except SheetsError as e:
                        yield _create_sse_message(
                            "log", message=f"Sheets書き込みエラー: {e.message}"
                        )
                    except Exception as e:
                        yield _create_sse_message(
                            "log", message=f"Sheets書き込みエラー: {str(e)}"
                        )

                except ScrapingError as e:
                    yield _create_sse_message(
                        "log", message=f"検索エラー: {e.message}"
                    )
                    continue

            yield _create_sse_message(
                "complete",
                clinics=all_valid_clinics,
                total_found=total_found,
                valid_count=len(all_valid_clinics),
                excluded_count=total_excluded,
                new_count=total_new,
            )

        except Exception as e:
            logger.exception("Scraping error")
            yield _create_sse_message("error", message=str(e))

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Nginx対応
        },
    )


@bp.route("/scrape/preview", methods=["POST"])
def scrape_preview() -> ResponseReturnValue:
    """
    スクレイピングプレビュー（Sheets書き込みなし）

    テスト用に検索と検証のみ実行
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    try:
        scrape_request = ScrapeRequest(**data)
    except PydanticValidationError as e:
        return jsonify({"success": False, "error": str(e)}), 400

    try:
        scraper = GoogleMapsScraper()
        exclusion_filter = ExclusionFilter()
        validator = ClaudeValidator()

        all_clinics = []

        for region in scrape_request.regions:
            query = f"{region} {scrape_request.search_suffix}"
            clinics = scraper.search(query)
            filtered = exclusion_filter.filter(clinics)
            all_clinics.extend(filtered)

        if not all_clinics:
            return jsonify(
                {
                    "success": True,
                    "clinics": [],
                    "total": 0,
                    "valid": 0,
                }
            )

        validated = validator.validate_batch(all_clinics)
        valid_clinics = [c for c in validated if c.get("is_valid", False)]

        return jsonify(
            {
                "success": True,
                "clinics": valid_clinics,
                "total": len(all_clinics),
                "valid": len(valid_clinics),
            }
        )

    except Exception as e:
        logger.exception("Preview error")
        return jsonify({"success": False, "error": str(e)}), 500
