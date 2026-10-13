"""Read-only operator dashboard. Authentication remains on the report APIs."""
from pathlib import Path

from fastapi.responses import FileResponse


def mount_dashboard(app):
    assets = Path(__file__).with_name("static")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard():
        # The public shell contains no operational data or credential. Static
        # assets are served by ui.mount_console under the existing CSP.
        return FileResponse(assets / "dashboard.html", headers={"Cache-Control": "no-store"})
