from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from turn_contract.models import ErrorBody, ErrorResponse


def install_error_handlers(app: FastAPI) -> None:
    """Map malformed requests to the contract's 400 ErrorResponse."""

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        body = ErrorResponse(error=ErrorBody(code="internal", message=f"bad request: {exc}"))
        return JSONResponse(status_code=400, content=body.model_dump())
