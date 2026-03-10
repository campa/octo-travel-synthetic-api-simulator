"""OCTO Error response model."""

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    error: str
    error_message: str = Field(alias="errorMessage")
    error_id: str = Field(alias="errorId")
