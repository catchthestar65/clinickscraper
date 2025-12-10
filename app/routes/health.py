"""ヘルスチェックエンドポイント"""

from flask import Blueprint, jsonify
from flask.typing import ResponseReturnValue

bp = Blueprint("health", __name__)


@bp.route("/health")
def health_check() -> ResponseReturnValue:
    """ヘルスチェック"""
    return jsonify({"status": "healthy"})


@bp.route("/ready")
def readiness_check() -> ResponseReturnValue:
    """レディネスチェック"""
    # TODO: データベースやサービスの接続確認を追加可能
    return jsonify({"status": "ready"})
