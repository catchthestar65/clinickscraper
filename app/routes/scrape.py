"""スクレイピングAPI"""

import json
import logging
import os
import time
import traceback
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


def _get_memory_mb() -> float:
    """現在のメモリ使用量をMB単位で取得"""
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        if os.uname().sysname == "Darwin":
            return rusage.ru_maxrss / (1024 * 1024)
        else:
            return rusage.ru_maxrss / 1024
    except Exception:
        return 0.0


def _create_sse_message(type_: str, **data) -> str:
    """SSEメッセージを作成"""
    message = {"type": type_, **data}
    return f"data: {json.dumps(message, ensure_ascii=False)}\n\n"


def _create_keepalive() -> str:
    """SSEキープアライブコメントを作成（接続維持用）"""
    return ": keepalive\n\n"


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
        session_start = time.time()
        last_keepalive = time.time()
        keepalive_interval = 15  # 15秒ごとにキープアライブ送信

        logger.info(f"[SESSION] ========== スクレイピングセッション開始 ==========")
        logger.info(f"[SESSION] 地域数: {len(scrape_request.regions)}")
        logger.info(f"[SESSION] 地域リスト: {scrape_request.regions}")
        logger.info(f"[SESSION] 検索キーワード: {scrape_request.search_suffix}")
        logger.info(f"[SESSION] 開始時メモリ: {_get_memory_mb():.1f} MB")

        # 開始メッセージを即座に送信（接続確認）
        yield _create_sse_message(
            "log",
            message=f"セッション開始 (地域数: {len(scrape_request.regions)})"
        )

        try:
            logger.info("[SESSION] サービス初期化中...")
            scraper = GoogleMapsScraper()
            exclusion_filter = ExclusionFilter()
            validator = ClaudeValidator()
            sheets_writer = SheetsWriter()
            logger.info("[SESSION] サービス初期化完了")

            all_valid_clinics = []
            total_found = 0
            total_excluded = 0
            total_new = 0

            # 各地域を検索（地域ごとに即時Sheets書き込み）
            for i, region in enumerate(scrape_request.regions):
                region_start = time.time()
                query = f"{region} {scrape_request.search_suffix}"

                logger.info(f"[REGION {i+1}/{len(scrape_request.regions)}] ========== 開始: {query} ==========")
                yield _create_sse_message(
                    "log",
                    message=f"[{i+1}/{len(scrape_request.regions)}] {query} で検索開始...",
                )

                try:
                    # キープアライブチェック
                    if time.time() - last_keepalive > keepalive_interval:
                        yield _create_keepalive()
                        last_keepalive = time.time()
                        logger.debug("[SESSION] キープアライブ送信")

                    logger.info(f"[REGION {i+1}] Google Maps検索開始...")
                    clinics = scraper.search(query)
                    logger.info(f"[REGION {i+1}] Google Maps検索完了: {len(clinics)}件")

                    yield _create_sse_message(
                        "log", message=f"検索結果: {len(clinics)}件取得"
                    )

                    if not clinics:
                        logger.info(f"[REGION {i+1}] 結果0件、次の地域へ")
                        yield _create_sse_message(
                            "log", message="→ スキップ（0件）"
                        )
                        continue

                    total_found += len(clinics)

                    # キープアライブチェック
                    if time.time() - last_keepalive > keepalive_interval:
                        yield _create_keepalive()
                        last_keepalive = time.time()

                    # キーワード除外
                    logger.info(f"[REGION {i+1}] キーワード除外フィルター適用中...")
                    filtered = exclusion_filter.filter(clinics)
                    excluded_count = len(clinics) - len(filtered)
                    total_excluded += excluded_count

                    if excluded_count > 0:
                        logger.info(f"[REGION {i+1}] キーワード除外: {excluded_count}件 (残り: {len(filtered)}件)")
                        yield _create_sse_message(
                            "log", message=f"キーワード除外: {excluded_count}件"
                        )

                    if not filtered:
                        logger.info(f"[REGION {i+1}] フィルタ後0件、次の地域へ")
                        yield _create_sse_message(
                            "log", message="→ スキップ（フィルタ後0件）"
                        )
                        continue

                    # キープアライブチェック
                    if time.time() - last_keepalive > keepalive_interval:
                        yield _create_keepalive()
                        last_keepalive = time.time()

                    # Claude API検証（地域ごと）
                    logger.info(f"[REGION {i+1}] Claude API検証開始: {len(filtered)}件")
                    yield _create_sse_message(
                        "log", message=f"Claude APIで検証中... ({len(filtered)}件)"
                    )

                    validated = validator.validate_batch(filtered)
                    valid_clinics = [c for c in validated if c.get("is_valid", False)]

                    logger.info(f"[REGION {i+1}] Claude API検証完了: 有効={len(valid_clinics)}件, 無効={len(validated)-len(valid_clinics)}件")
                    yield _create_sse_message(
                        "log", message=f"検証完了: 有効 {len(valid_clinics)}件"
                    )

                    if not valid_clinics:
                        logger.info(f"[REGION {i+1}] 有効クリニック0件、次の地域へ")
                        yield _create_sse_message(
                            "log", message="→ スキップ（有効0件）"
                        )
                        continue

                    # キープアライブチェック
                    if time.time() - last_keepalive > keepalive_interval:
                        yield _create_keepalive()
                        last_keepalive = time.time()

                    # Google Sheets書き込み（地域ごとに即時保存）
                    logger.info(f"[REGION {i+1}] Google Sheets書き込み開始...")
                    try:
                        new_count = sheets_writer.append(valid_clinics)
                        total_new += new_count
                        logger.info(f"[REGION {i+1}] Sheets書き込み完了: 新規={new_count}件 (累計: {total_new}件)")
                        yield _create_sse_message(
                            "log",
                            message=f"→ Sheets保存: 新規{new_count}件 (累計: {total_new}件)",
                        )
                        all_valid_clinics.extend(valid_clinics)
                    except SheetsError as e:
                        logger.error(f"[REGION {i+1}] Sheets書き込みエラー: {e.message}")
                        yield _create_sse_message(
                            "log", message=f"[WARN] Sheets書き込みエラー: {e.message}"
                        )
                    except Exception as e:
                        logger.error(f"[REGION {i+1}] Sheets書き込みエラー: {type(e).__name__}: {e}")
                        logger.error(f"[REGION {i+1}] スタックトレース:\n{traceback.format_exc()}")
                        yield _create_sse_message(
                            "log", message=f"[WARN] Sheets書き込みエラー: {str(e)}"
                        )

                except ScrapingError as e:
                    logger.error(f"[REGION {i+1}] スクレイピングエラー: {e.message}")
                    yield _create_sse_message(
                        "log", message=f"[WARN] 検索エラー: {e.message}"
                    )
                    continue
                except Exception as e:
                    logger.error(f"[REGION {i+1}] 予期せぬエラー: {type(e).__name__}: {e}")
                    logger.error(f"[REGION {i+1}] スタックトレース:\n{traceback.format_exc()}")
                    yield _create_sse_message(
                        "log", message=f"[ERROR] 予期せぬエラー: {type(e).__name__}: {e}"
                    )
                    continue
                finally:
                    region_elapsed = time.time() - region_start
                    mem_mb = _get_memory_mb()
                    logger.info(f"[REGION {i+1}] ========== 完了: {region_elapsed:.1f}秒, メモリ: {mem_mb:.1f} MB ==========")
                    yield _create_sse_message(
                        "log", message=f"地域完了: {region_elapsed:.1f}秒"
                    )

            session_elapsed = time.time() - session_start
            mem_mb = _get_memory_mb()
            logger.info(f"[SESSION] ========== セッション完了 ==========")
            logger.info(f"[SESSION] 総時間: {session_elapsed:.1f}秒")
            logger.info(f"[SESSION] 検索結果: {total_found}件")
            logger.info(f"[SESSION] 除外: {total_excluded}件")
            logger.info(f"[SESSION] 有効: {len(all_valid_clinics)}件")
            logger.info(f"[SESSION] 新規保存: {total_new}件")
            logger.info(f"[SESSION] 最終メモリ: {mem_mb:.1f} MB")

            yield _create_sse_message(
                "complete",
                clinics=all_valid_clinics,
                total_found=total_found,
                valid_count=len(all_valid_clinics),
                excluded_count=total_excluded,
                new_count=total_new,
            )

        except Exception as e:
            logger.exception(f"[SESSION] 致命的エラー: {type(e).__name__}: {e}")
            logger.error(f"[SESSION] スタックトレース:\n{traceback.format_exc()}")
            logger.error(f"[SESSION] メモリ使用量: {_get_memory_mb():.1f} MB")
            yield _create_sse_message("error", message=f"致命的エラー: {type(e).__name__}: {e}")

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
