"""Flask application factory."""
import os
from pathlib import Path

from flask import Flask, render_template
from flask_cors import CORS


def create_app() -> Flask:
    root = Path(__file__).parent.parent
    app = Flask(
        __name__,
        template_folder=str(root / "templates"),
        static_folder=str(root / "static"),
    )
    app.secret_key = os.urandom(32)
    CORS(app)

    from .models          import bp as models_bp
    from .datasource      import bp as datasource_bp
    from .chat            import bp as chat_bp
    from .saved_sessions  import bp as saved_sessions_bp
    from .system          import bp as system_bp

    app.register_blueprint(models_bp)
    app.register_blueprint(datasource_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(saved_sessions_bp)
    app.register_blueprint(system_bp)

    @app.get("/")
    def index():
        return render_template("agent_chat.html")

    return app
