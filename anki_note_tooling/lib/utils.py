import uuid
from pathlib import Path


def get_unique_path(*, dir: Path, filename: str) -> Path:
    base_path = dir / filename
    return base_path.parent / f"{base_path.stem}-{uuid.uuid4()}{base_path.suffix}"
