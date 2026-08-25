from pydantic import BaseModel


class DetailResponse(BaseModel):
    """Acknowledgement body for endpoints with nothing to return.

    Deliberately not 204: the client reads every response with res.json(), which
    rejects on an empty body.
    """
    detail: str


class HealthResponse(BaseModel):
    status: str
