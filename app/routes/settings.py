"""設定管理API"""

import logging
from flask import Blueprint, request, jsonify
from flask.typing import ResponseReturnValue

from app.config import config
from app.services.sheets_writer import SheetsWriter
from app.services.exclusion_filter import ExclusionFilter

logger = logging.getLogger(__name__)
bp = Blueprint("settings", __name__, url_prefix="/api/settings")


@bp.route("/", methods=["GET"])
def get_settings() -> ResponseReturnValue:
    """現在の設定を取得"""
    return jsonify(
        {
            "project_name": config.project_name,
            "search_suffix": config.search_suffix,
            "max_regions_per_batch": config.max_regions_per_batch,
            "exclusion_keywords": config.exclusion_keywords,
            "google_sheets": {
                "spreadsheet_id": config.google_sheets_id,
                "sheet_name": config.google_sheets_name,
            },
        }
    )


@bp.route("/", methods=["POST"])
def update_settings() -> ResponseReturnValue:
    """設定を更新"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    try:
        # デフォルト設定更新
        config.update_default_config(
            project_name=data.get("project_name"),
            search_suffix=data.get("search_suffix"),
            sheet_name=data.get("sheet_name"),
        )

        # 除外キーワード更新
        if "exclusion_keywords" in data:
            config.update_exclusion_keywords(data["exclusion_keywords"])

        logger.info("Settings updated successfully")
        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Failed to update settings: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/test-sheets", methods=["POST"])
def test_sheets_connection() -> ResponseReturnValue:
    """Google Sheets接続テスト"""
    try:
        writer = SheetsWriter()
        result = writer.test_connection()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Sheets connection test failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/exclusion-keywords", methods=["GET"])
def get_exclusion_keywords() -> ResponseReturnValue:
    """除外キーワード一覧取得"""
    return jsonify({"keywords": config.exclusion_keywords})


@bp.route("/exclusion-keywords", methods=["POST"])
def add_exclusion_keyword() -> ResponseReturnValue:
    """除外キーワード追加"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"success": False, "error": "Keyword is required"}), 400

    exclusion_filter = ExclusionFilter()
    exclusion_filter.add_keyword(keyword)
    exclusion_filter.save()

    return jsonify({"success": True, "keywords": exclusion_filter.keywords})


@bp.route("/exclusion-keywords", methods=["DELETE"])
def remove_exclusion_keyword() -> ResponseReturnValue:
    """除外キーワード削除"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    keyword = data.get("keyword", "")
    if not keyword:
        return jsonify({"success": False, "error": "Keyword is required"}), 400

    exclusion_filter = ExclusionFilter()
    exclusion_filter.remove_keyword(keyword)
    exclusion_filter.save()

    return jsonify({"success": True, "keywords": exclusion_filter.keywords})
