"""ヘルスチェックエンドポイント"""

import os
import platform
import sys
from datetime import datetime

from flask import Blueprint, jsonify
from flask.typing import ResponseReturnValue

bp = Blueprint("health", __name__)


def _get_memory_info() -> dict:
    """メモリ情報を取得"""
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        if os.uname().sysname == "Darwin":
            max_rss_mb = rusage.ru_maxrss / (1024 * 1024)
        else:
            max_rss_mb = rusage.ru_maxrss / 1024
        return {
            "max_rss_mb": round(max_rss_mb, 1),
            "user_time_sec": round(rusage.ru_utime, 2),
            "system_time_sec": round(rusage.ru_stime, 2),
        }
    except Exception as e:
        return {"error": str(e)}


@bp.route("/health")
def health_check() -> ResponseReturnValue:
    """ヘルスチェック"""
    return jsonify({"status": "healthy"})


@bp.route("/ready")
def readiness_check() -> ResponseReturnValue:
    """レディネスチェック"""
    return jsonify({"status": "ready"})


@bp.route("/debug")
def debug_info() -> ResponseReturnValue:
    """デバッグ情報エンドポイント"""
    memory = _get_memory_info()

    return jsonify({
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "memory": memory,
        "system": {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "python_version": sys.version,
            "pid": os.getpid(),
        },
        "environment": {
            "FLASK_ENV": os.environ.get("FLASK_ENV", "not set"),
            "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "not set"),
        }
    })
