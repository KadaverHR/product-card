#!/usr/bin/env python3
"""Download a YML offer and build product cards using the existing template."""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

from banner_card import compose
from build import ROOT, cutout
from PIL import Image

DEFAULT_FEED = 'https://3sta.ru/yandex/feed.xml'


class IncompleteOffer(ValueError):
    """An offer cannot be rendered because required input data is absent."""


def validate_product(product):
    card = product.get('card') or {}
    missing = [key for key in ('brand', 'model', 'size', 'diameter', 'load', 'speed')
               if card.get(key) is None or not str(card.get(key, '')).strip()]
    if not str(product.get('id') or '').strip():
        missing.append('id')
    pictures = product.get('pictures') or []
    if not pictures or not str(pictures[0] or '').strip():
        missing.append('picture')
    if missing:
        raise IncompleteOffer('Missing required data: ' + ', '.join(missing))


def open_url(url, timeout):
    if urlparse(url).scheme not in ('http', 'https'):
        raise ValueError('URL must use http or https')
    return urllib.request.urlopen(urllib.request.Request(
        url, headers={'User-Agent': 'ProductCard/1.0'}), timeout=timeout)


def tag(element):
    return element.tag.rsplit('}', 1)[-1]


def read_offers(source, offer_id=None, limit=1, timeout=30):
    """Stream XML; release processed elements and stop at the requested offer."""
    stream = open_url(source, timeout) if urlparse(source).scheme in ('http', 'https') else open(source, 'rb')
    count = 0
    with stream:
        stack = []
        for event, element in ET.iterparse(stream, events=('start', 'end')):
            if event == 'start':
                stack.append(element)
                continue
            if tag(element) == 'offer':
                if offer_id is None or element.get('id') == offer_id:
                    yield offer_data(element, source)
                    count += 1
                    if offer_id is not None or (limit and count >= limit):
                        return
                if len(stack) > 1:
                    stack[-2].remove(element)
                element.clear()
            stack.pop()
    if offer_id is not None and not count:
        raise ValueError(f'Offer ID {offer_id!r} was not found')
    if not count:
        raise ValueError('Feed contains no offers')


def offer_data(element, source):
    # Preserve repeated fields, attributes, units and nested content in the export.
    fields = []
    for child in element:
        fields.append({'tag': tag(child), 'attributes': dict(child.attrib),
                       'value': ''.join(child.itertext()).strip(),
                       'xml': ET.tostring(child, encoding='unicode')})
    def value(name):
        return next((f['value'] for f in fields if f['tag'] == name), '')
    params = [f for f in fields if f['tag'] == 'param']
    by_name = {p['attributes'].get('name', '').casefold(): p['value'] for p in params}
    def param(name):
        return by_name.get(name.casefold(), '')
    name, brand = value('name'), value('vendor')
    size = param('Размер')
    match = re.search(r'(\d{3})\s*/\s*(\d{2,3})\s*R\s*(\d{2})(?:\s+(\d{2,3})([A-Z]))?', size or name, re.I)
    width = param('Ширина') or (match[1] if match else '')
    profile = param('Профиль') or (match[2] if match else '')
    diameter = param('Диаметр') or (match[3] if match else '')
    model = value('model') or name
    if brand and model.casefold().startswith(brand.casefold()):
        model = model[len(brand):].strip()
    model = re.split(r'\b\d{3}\s*/\s*\d{2,3}', model, maxsplit=1)[0].strip()
    season = param('Сезон').casefold()
    label = {'летняя': 'ЛЕТНИЕ ШИНЫ', 'зимняя': 'ЗИМНИЕ ШИНЫ',
             'всесезонная': 'ВСЕСЕЗОННЫЕ ШИНЫ'}.get(season, 'ШИНЫ')
    card = {'brand': brand, 'model': model, 'size': f'{width}/{profile}' if width and profile else '',
            'diameter': 'R' + diameter.lstrip('Rr') if diameter else '',
            'load': param('Индекс нагрузки') or (match[4] or '' if match else ''),
            'speed': param('Индекс скорости') or (match[5] or '' if match else ''),
            'season_label': label}
    pictures = [urljoin(source, f['value']) for f in fields if f['tag'] == 'picture' and f['value']]
    return {'id': element.get('id', ''), 'attributes': dict(element.attrib),
            'fields': fields, 'parameters': params, 'pictures': pictures, 'card': card}


def generate(product, args):
    validate_product(product)
    product_id = product['id']
    if not re.fullmatch(r'[\w-]+', product_id) or product_id.upper() in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9]', product_id, re.I):
        raise ValueError('Offer ID is unsuitable for an output directory')
    folder = args.output.with_name(args.output.name+'-sources')/product_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'product.json').write_text(json.dumps(product, ensure_ascii=False, indent=2), encoding='utf-8')
    image_path = folder/'source.img'
    with open_url(product['pictures'][0], args.timeout) as response, image_path.open('wb') as target:
        total = 0
        while chunk := response.read(65536):
            total += len(chunk)
            if total > 30 * 1024 * 1024:
                raise ValueError('Product image exceeds 30 MB')
            target.write(chunk)
    with Image.open(image_path) as image:
        image.verify()
    tyre = cutout(image_path, args.threshold)
    for theme in (['dark', 'light'] if args.theme == 'all' else [args.theme]):
        background = args.dark_background if theme == 'dark' else args.background
        theme_folder = args.output/theme
        theme_folder.mkdir(parents=True, exist_ok=True)
        target = theme_folder/f'{product_id}.png'
        compose(tyre, product['card'], background, theme, enhance=not args.raw_photo).save(target)
        print(f'{product_id}: {target.resolve()}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feed', default=DEFAULT_FEED, help='Feed URL or local XML file')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--id', help='Exact offer ID, e.g. S207352')
    selection.add_argument('--limit', type=int, default=1, help='First N offers; 0 means entire feed (default: 1)')
    parser.add_argument('--output', type=Path, default=ROOT/'feed-output')
    parser.add_argument('--theme', choices=['dark', 'light', 'all'], default='all')
    parser.add_argument('--background', type=Path, default=ROOT/'assets/light-paint-background-v8.png')
    parser.add_argument('--dark-background', type=Path, default=ROOT/'assets/dark-paint-background-v7.png')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--threshold', type=int, default=235)
    parser.add_argument('--raw-photo', action='store_true', help='Disable tyre tone and sharpness preset')
    args = parser.parse_args()
    if args.limit < 0 or args.timeout <= 0 or not 0 <= args.threshold <= 255:
        parser.error('limit must be >= 0, timeout > 0, threshold between 0 and 255')
    for theme in ('dark', 'light'):
        (args.output/theme).mkdir(parents=True, exist_ok=True)
    processed = generated = skipped = failed = 0
    try:
        for product in read_offers(args.feed, args.id, args.limit, args.timeout):
            processed += 1
            product_id = product.get('id') or '<missing-id>'
            try:
                generate(product, args)
                generated += 1
            except IncompleteOffer as error:
                skipped += 1
                print(f'SKIP {product_id}: {error}', flush=True)
            except Exception as error:
                failed += 1
                print(f'ERROR {product_id}: {error}', file=sys.stderr, flush=True)
    except Exception as error:
        print(f'Feed error: {error}', file=sys.stderr, flush=True)
        print(f'Summary: processed={processed}, generated={generated}, skipped={skipped}, failed={failed}', flush=True)
        return 1
    print(f'Summary: processed={processed}, generated={generated}, skipped={skipped}, failed={failed}', flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
