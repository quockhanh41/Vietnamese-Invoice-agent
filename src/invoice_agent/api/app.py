"""The FastAPI application — routes split for real (design §11.2).

Project A's API was one 643-line module beside an empty `routes/` package. This
one is split from the first commit, one module per route group.

NO GLOBAL `ValueError` HANDLER, deliberately. It would catch genuine bugs and
serve them as 422 client errors — the API equivalent of a bare `except`. M5-B's
review found a bare `assert` in `serial_join` that killed a whole batch and was
invisible under replay; a broad handler here is the same mistake with a
friendlier status code. Routes raise `HTTPException` at the call site instead.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from invoice_agent.api.routes import health
from invoice_agent.tools.guards import GuardDenied


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(GuardDenied)
    def _guard_denied(_request: Request, exc: GuardDenied) -> JSONResponse:
        # 409, not 500. The guard refusing is the system working; a 500 would
        # read as a bug and bury a working safety control in the logs. The
        # guard has already written its own `audit_log` row on its own
        # connection, so the refusal is durable regardless of this response.
        return JSONResponse(status_code=409, content={"detail": str(exc)})


def create_app() -> FastAPI:
    app = FastAPI(
        title="Vietnamese Invoice & Expense Agent",
        version="0.1.0",
    )
    install_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
