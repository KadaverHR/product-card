"""Shared atomic JPEG publishing for generated product cards."""
import os
import tempfile
from pathlib import Path

JPEG_QUALITY = 90


def save_card(image, destination):
    destination = Path(destination)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.card-', suffix='.tmp', delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        image.convert('RGB').save(staging, format='JPEG', quality=JPEG_QUALITY,
                                  subsampling=0, optimize=True, progressive=True)
        os.replace(staging, destination)
    finally:
        staging.unlink(missing_ok=True)
