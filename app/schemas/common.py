from typing import TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class MessageResponse(BaseModel):
    message: str


class PaginatedResponse[T](BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    total: int
    limit: int
    offset: int

