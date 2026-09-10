import shutil
import subprocess
from pathlib import Path
from typing import Optional


def clean_file_name(name: str) -> str:
    """
    The name a media file takes inside `collection.media`.

    Anki's field markup gives `[` and `]` and whitespace their own meaning, so a filename
    carrying them cannot be referenced from `[sound:...]` or `<img src=...>`.

    >>> clean_file_name("a [strange] name.mp3")
    'astrangename.mp3'
    """
    return name.replace(" ", "").replace("[", "").replace("]", "")


def _ffmpeg_command(
    input_file: Path, output_file: Path, volume_scale: Optional[float] = None
) -> list[str]:
    """Build an ffmpeg command list for audio processing.

    >>> input_path = Path("input.wav")
    >>> output_path = Path("output.mp3")
    >>> _ffmpeg_command(input_path, output_path)
    ['ffmpeg', '-i', 'input.wav', 'output.mp3']
    >>> _ffmpeg_command(input_path, output_path, volume_scale=0.5)
    ['ffmpeg', '-i', 'input.wav', '-filter:a', 'volume=0.50', 'output.mp3']
    >>> _ffmpeg_command(input_path, output_path, volume_scale=1.75)
    ['ffmpeg', '-i', 'input.wav', '-filter:a', 'volume=1.75', 'output.mp3']
    """
    if volume_scale is None:
        return ["ffmpeg", "-i", str(input_file), str(output_file)]
    else:
        return [
            "ffmpeg",
            "-i",
            str(input_file),
            "-filter:a",
            f"volume={volume_scale:.2f}",
            str(output_file),
        ]


def create_mp3_file(*, input_file: Path, output_file: Path, volume_scale: Optional[float] = None):
    if output_file.is_dir():
        raise IsADirectoryError(f"Cannot create MP3 file at {output_file} as it is a directory")
    elif output_file.exists():
        print(f"Not creating MP3 file at {output_file} as it already exists")
    elif input_file.suffix == ".mp3" and volume_scale is None:
        shutil.copy(input_file, output_file)
        print(f"Copied {input_file} to {output_file}")
    else:
        with open("/dev/null") as dev_null:
            command = _ffmpeg_command(
                input_file=input_file, output_file=output_file, volume_scale=volume_scale
            )
            print(command)
            subprocess.run(
                command,
                stdout=dev_null,
                stderr=dev_null,
            )
            print(f"Created {output_file} from {input_file} with volume_scale={volume_scale}")
