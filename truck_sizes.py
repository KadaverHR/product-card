"""Tolerant display-only splitting of truck sizes; never invent a diameter."""
import re
from tyre_sizes import TyreSize, parse_size


def split_truck_size(value, diameter=None):
    raw = str(value).strip()
    if not raw:
        raise ValueError('Truck size must not be empty')
    if diameter is not None and str(diameter).strip():
        badge = str(diameter).strip()
        # An explicit diameter is authoritative; avoid repeating it in the size.
        if raw.casefold().endswith(badge.casefold()):
            prefix = raw[:-len(badge)].rstrip()
            if prefix:
                return TyreSize(prefix, badge)
        return TyreSize(raw, badge)
    try:
        return parse_size(raw)
    except ValueError:
        pass
    # Work backwards so '-' in a model-like width token is not the diameter.
    matches = list(re.finditer(r'(?:ZR|[RР]|[-–—])\s*\d{1,3}(?:[.,]\d+)?', raw, re.I))
    if matches:
        match = matches[-1]
        prefix, badge = raw[:match.start()].strip(), raw[match.start():].strip()
        tail = raw[match.end():].strip()
        # A numeric remainder may be load indices, not part of the diameter.
        if prefix and (not tail or re.fullmatch(r'[A-Za-zА-Яа-я+\s.\'"″-]+', tail)):
            return TyreSize(prefix, badge)
    # Sizes sometimes separate the diameter only with a space. Do not add 'R'.
    match = re.fullmatch(r'(.+?)\s+(\d{1,2}(?:[.,]\d+)?(?:\s*[A-Za-z]+)?)', raw)
    if match and re.search(r'\d', match[1]):
        return TyreSize(match[1].strip(), match[2].strip())
    # Ambiguous input remains usable and visible, without a fabricated badge.
    return TyreSize(raw, '')
