"""Original agricultural and industrial background assets."""
from pathlib import Path
from PIL import Image

ASSETS = Path(__file__).resolve().parent/'assets'


def category_background(product_type, theme, size=(1200,1600)):
    if product_type not in ('agricultural','special') or theme not in ('dark','light'):
        raise ValueError('Expected agricultural/special and dark/light')
    suffix = '-light' if theme == 'light' else ''
    path = ASSETS/f'{product_type}-background{suffix}-v4.png'
    with Image.open(path) as source:
        return source.convert('RGBA').resize(size,Image.Resampling.LANCZOS)
