"""Flaskアプリケーションエントリーポイント"""

import logging
from flask import Flask, render_template
from flask.typing import ResponseReturnValue

from app.config import config
from app.routes import health_bp, settings_bp, scrape_bp

# ロギング設定
logging.basicConfig(
    level=logging.DEBUG if config.flask_env == "development" else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """Flaskアプリケーションファクトリ"""
    app = Flask(__name__)

    # 設定
    app.secret_key = config.secret_key
    app.config["JSON_AS_ASCII"] = False  # 日本語をそのまま出力

    # Blueprint登録
    app.register_blueprint(health_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(scrape_bp)

    # ルートページ
    @app.route("/")
    def index() -> ResponseReturnValue:
        return render_template("index.html")

    @app.route("/settings")
    def settings_page() -> ResponseReturnValue:
        return render_template("settings.html")

    # エラーハンドラー
    @app.errorhandler(404)
    def not_found(e: Exception) -> ResponseReturnValue:
        return {"error": "Not found"}, 404

    @app.errorhandler(500)
    def internal_error(e: Exception) -> ResponseReturnValue:
        logger.exception("Internal server error")
        return {"error": "Internal server error"}, 500

    logger.info(f"Flask app created (env: {config.flask_env})")
    return app


# グローバルアプリインスタンス（gunicorn用）
app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
