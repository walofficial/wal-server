from pydantic import BaseModel


class ImageWithDims(BaseModel):
    url: str
    width: int
    height: int
    aspectRatio: dict
