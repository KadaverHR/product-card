"""Tyre size syntax shared by feed parsing and API validation."""
import re
from dataclasses import dataclass

SUFFIX = r'(?:C)?(?:\s*(?:TL|LT)){0,2}'
SIZE_PATTERN = (
    r'(?<![\w.])(?:'
    r'(?P<inch>\d{2,3}(?:[.,]\d+)?\s*[xх×]\s*\d{1,2}(?:[.,]\d+)?)\s*(?P<inch_sep>[-R])\s*'
    r'(?P<inch_diameter>\d{2}(?:[.,]\d+)?)'
    r'|(?P<narrow>\d{1,2}[.,]\d{1,2})\s*R\s*(?P<narrow_diameter>\d{2}(?:[.,]\d+)?)'
    r'|(?P<width>\d{2,3})(?:\s*/\s*(?P<profile>\d{2,3}))?\s*(?P<metric_sep>[-R])\s*'
    r'(?P<diameter>\d{2}(?:[.,]\d+)?))'
    r'(?P<suffix>' + SUFFIX + r')(?![\w.])'
)
SIZE_RE = re.compile(SIZE_PATTERN, re.I)


@dataclass(frozen=True)
class TyreSize:
    size: str
    diameter: str

    @property
    def normalized(self):
        return f'{self.size} {self.diameter}'


def from_match(match):
    suffix = re.sub(r'\s+', '', match['suffix']).upper()
    if match['inch']:
        size = re.sub(r'\s+', '', match['inch']).lower().replace('х', 'x').replace('×', 'x').replace(',', '.')
        diameter = match['inch_sep'].upper() + match['inch_diameter'].replace(',', '.') + suffix
    elif match['narrow']:
        size = match['narrow'].replace(',', '.')
        diameter = 'R' + match['narrow_diameter'].replace(',', '.') + suffix
    else:
        if not match['profile'] and len(match['width']) != 3:
            raise ValueError('A metric size without a profile must have a three-digit width')
        size = match['width'] + ('/' + match['profile'] if match['profile'] else '')
        diameter = match['metric_sep'].upper() + match['diameter'].replace(',', '.') + suffix
    return TyreSize(size, diameter)


def parse_size(value):
    match = SIZE_RE.fullmatch(value.strip())
    if not match:
        raise ValueError('Размер: 225/65 R17, 255/85-16, 195 R14C, 5.5 R13C или 33x10.5-15 LT; индексы передавайте отдельно')
    return from_match(match)
