"""ルート定義"""

from app.routes.health import bp as health_bp
from app.routes.settings import bp as settings_bp
from app.routes.scrape import bp as scrape_bp

__all__ = ["health_bp", "settings_bp", "scrape_bp"]
