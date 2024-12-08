from pathlib import Path
from typing import Tuple

from fastapi import UploadFile


def add_system_message(text: str):
    return {"role": "system", "content": text}


def add_user_message(text: str):
    return {"role": "user", "content": text}


def add_tool_function(function):
    return {"type": "function", "function": function}


def get_file_name_and_extension(file: UploadFile) -> Tuple[str, str]:
    path = Path(file.filename)
    file_extension = path.suffix
    file_name = path.stem
    return file_name, file_extension
