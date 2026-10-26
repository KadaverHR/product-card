"""Shared atomic JPEG publishing for generated product cards."""
import os
import tempfile
from pathlib import Path
from PIL import Image

JPEG_QUALITY = 82
MAX_EDGE = 1200


def compression_profile(quality=None, max_edge=None, subsampling=None):
    quality = int(quality if quality is not None else os.getenv('CARD_JPEG_QUALITY', JPEG_QUALITY))
    max_edge = int(max_edge if max_edge is not None else os.getenv('CARD_MAX_EDGE', MAX_EDGE))
    subsampling = int(subsampling if subsampling is not None else os.getenv('CARD_JPEG_SUBSAMPLING', 2))
    if not 1 <= quality <= 95 or max_edge < 1 or subsampling not in (0, 1, 2):
        raise ValueError('quality must be 1..95, max_edge > 0, subsampling must be 0, 1 or 2')
    return quality, max_edge, subsampling


def save_card(image, destination, *, quality=None, max_edge=None, subsampling=None, only_smaller=False):
    quality, max_edge, subsampling = compression_profile(quality, max_edge, subsampling)
    destination = Path(destination)
    image = image.convert('RGB')
    if max(image.size) > max_edge:
        image.thumbnail((max_edge, max_edge), resample=Image.Resampling.LANCZOS)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.card-', suffix='.tmp', delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        image.save(staging, format='JPEG', quality=quality,
                   subsampling=subsampling, optimize=True, progressive=True)
        # Verify the full output before replacing a working file.
        with Image.open(staging) as check:
            check.load()
            if check.size != image.size or check.format != 'JPEG':
                raise ValueError('JPEG verification failed')
        if only_smaller and destination.is_file() and staging.stat().st_size >= destination.stat().st_size:
            return False
        os.replace(staging, destination)
        return True
    finally:
        staging.unlink(missing_ok=True)
