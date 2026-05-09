"""Blueprint: file download endpoint for exported Excel / Word files."""
import os
import re

from flask import Blueprint, send_file, abort

bp = Blueprint("output", __name__)

_EXPORT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "outputs", "exports"
)


@bp.get("/api/export/<path:filename>")
def download_export(filename: str):
    """Serve an exported file from outputs/exports/.

    Security: only filenames composed of word chars, hyphens, and dots
    are accepted to prevent path traversal.
    """
    if not re.match(r'^[\w\-\.]+$', filename):
        abort(400)

    filepath = os.path.join(_EXPORT_DIR, filename)
    if not os.path.isfile(filepath):
        abort(404)

    return send_file(filepath, as_attachment=True, download_name=filename)
