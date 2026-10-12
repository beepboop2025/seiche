"""Owner and agent console; credentials exist only in the browser page's memory."""
from pathlib import Path
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_console(app):
    assets = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=assets), name="network-static")

    @app.get("/console", include_in_schema=False)
    def console():
        return FileResponse(assets / "console.html", headers={"Cache-Control": "no-store"})

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(assets / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/privacy", include_in_schema=False)
    def privacy():
        return FileResponse(assets / "privacy.html", headers={"Cache-Control": "no-store"})

    @app.get("/connect", include_in_schema=False)
    def connect():
        return FileResponse(assets / "connect.html", headers={"Cache-Control": "no-store"})

    from .dashboard import mount_dashboard
    mount_dashboard(app)
