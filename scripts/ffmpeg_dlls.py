"""Register FFmpeg DLLs so torchcodec can load on Windows.

Python >= 3.8 on Windows ignores PATH for dependent DLLs; torchcodec's
libtorchcodec_core*.dll needs avcodec/avutil/swresample next to it or
registered via os.add_dll_directory() BEFORE torchcodec imports.

Usage (must run before any `import TTS` / `import torchcodec`):

    import ffmpeg_dlls  # noqa: F401
"""

import os
from pathlib import Path

_CANDIDATES = [
    Path(__file__).resolve().parent / "ffmpeg_dlls",  # vendored next to this file
    Path("./ffmpeg_build").resolve(),  # extracted build root
]


def _dll_dirs():
    for candidate in _CANDIDATES:
        if candidate.is_dir():
            dlls = list(candidate.glob("avcodec-*.dll"))
            if dlls or (candidate / "bin").is_dir():
                yield candidate
                bin_dir = candidate / "bin"
                if bin_dir.is_dir():
                    yield bin_dir
                return


def _register():
    for directory in _dll_dirs():
        try:
            os.add_dll_directory(str(directory))
        except (OSError, AttributeError):
            pass


_register()
