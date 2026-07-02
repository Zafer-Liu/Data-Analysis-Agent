"""Flask application factory."""
import logging
import os
from urllib.parse import urlsplit

from flask import Flask, abort, render_template, request
from flask_cors import CORS
from infrastructure.paths import resource_path

log = logging.getLogger(__name__)


def _start_background_services() -> None:
    """Start long-lived background services (local only, skipped on Vercel)."""
    if os.environ.get("VERCEL"):
        return
    if not resource_path("MCP").is_dir():
        log.info("[startup] bundled MCP resources are not installed; continuing without them")
        return
    try:
        from MCP.flowchart_server import ensure_flowchart_server
        ensure_flowchart_server()
    except Exception as e:
        log.warning("[startup] flowchart server: %s", e)


def _run_startup_hooks() -> None:
    try:
        from agent.hooks.models import HookContext
        from data.hooks_store import load_engine

        engine = load_engine()
        engine.run_hooks("startup", HookContext(event_name="startup"))
    except Exception as exc:
        log.warning("[startup] hooks skipped: %s", exc)


def create_app() -> Flask:
    _start_background_services()

    app = Flask(
        __name__,
        template_folder=str(resource_path("templates")),
        static_folder=str(resource_path("static")),
    )
    app.secret_key = os.urandom(32)
    local_origins = [
        r"http://localhost(?::\d+)?",
        r"http://127\.0\.0\.1(?::\d+)?",
        r"http://\[::1\](?::\d+)?",
    ]
    CORS(
        app,
        resources={r"/api/*": {"origins": local_origins}},
        allow_headers=["Content-Type", "X-Requested-With"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

    from .models          import bp as models_bp
    from .datasource      import bp as datasource_bp
    from .chat            import bp as chat_bp
    from .saved_sessions  import bp as saved_sessions_bp
    from .system          import bp as system_bp
    from .output          import bp as output_bp
    from .mcp             import bp as mcp_bp
    from .dashboard       import bp as dashboard_bp
    from .knowledge       import bp as knowledge_bp
    from .workspace       import bp as workspace_bp
    from .jobs            import bp as jobs_bp
    from .skills          import bp as skills_bp
    from .commands        import bp as commands_bp
    from .desktop         import bp as desktop_bp
    from .hooks           import bp as hooks_bp
    from .teams           import bp as teams_bp

    app.register_blueprint(models_bp)
    app.register_blueprint(datasource_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(saved_sessions_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(output_bp)
    app.register_blueprint(mcp_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(knowledge_bp)
    app.register_blueprint(workspace_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(skills_bp)
    app.register_blueprint(commands_bp)
    app.register_blueprint(desktop_bp)
    app.register_blueprint(hooks_bp)
    app.register_blueprint(teams_bp)
    _run_startup_hooks()

    @app.before_request
    def reject_cross_origin_writes():
        """Block browser writes from an unrelated site while preserving CLI use."""
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        origin = (request.headers.get("Origin") or "").strip()
        if not origin:
            return None
        try:
            parsed = urlsplit(origin)
            same_origin = parsed.netloc.lower() == request.host.lower()
            local_origin = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        except ValueError:
            same_origin = local_origin = False
        if not same_origin and not local_origin:
            abort(403, description="Cross-origin write rejected")
        return None

    @app.get("/")
    def index():
        return render_template("agent_chat.html")

    @app.get("/api/health")
    def health():
        """Minimal desktop-launch readiness probe; never expose local config."""
        return {
            "ok": True,
            "status": "healthy",
            "service": "business-analytics-agent",
        }

    @app.after_request
    def add_security_headers(response):
        """Apply a restrictive browser baseline while allowing generated charts."""
        is_chart = request.path.startswith("/api/chart/")
        if is_chart:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; "
                "font-src 'self' data:; "
                "connect-src 'none'; "
                "base-uri 'none'; "
                "form-action 'none'; "
                "frame-ancestors 'self'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; "
                "font-src 'self' data:; "
                "connect-src 'self'; "
                "frame-src 'self'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'"
            )
            response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        return response

    return app
