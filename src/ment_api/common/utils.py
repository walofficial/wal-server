from pathlib import Path
from typing import Tuple

from fastapi import UploadFile


def get_file_name_and_extension(file: UploadFile) -> Tuple[str, str]:
    path = Path(file.filename)
    file_extension = path.suffix
    file_name = path.stem
    return file_name, file_extension
