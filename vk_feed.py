"""Convert a Yandex YML feed for import into VK community products. Python 3.10+."""
import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import xml.etree.ElementTree as ET

SOURCE = 'https://3sta.ru/yandex/feed-truck.xml'
MAX_BYTES = 8_000_000  # Conservative interpretation of 8 MB.


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('br', 'p', 'div', 'li', 'tr') and not self.hidden:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        if tag in ('p', 'div', 'li', 'tr') and not self.hidden:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(value or '')
    value = ''.join(parser.parts).replace('\xa0', ' ')
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value)
    value = '\n'.join(re.sub(r'[ \t\r]+', ' ', s).strip() for s in value.split('\n'))
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def web_url(value):
    try:
        parts = urlsplit(value)
        return parts.scheme in ('http', 'https') and bool(parts.hostname)
    except ValueError:
        return False


def money(value):
    try:
        number = Decimal((value or '').strip())
        if not number.is_finite() or number <= 0 or number != number.to_integral_value():
            raise ValueError('price must be a positive integer; automatic rounding is disabled')
        return str(int(number))
    except InvalidOperation as exc:
        raise ValueError('invalid price') from exc


def convert(data):
    # Do not accept internal entities/DTDs from an external feed.
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('DTD/entities are not supported')
    root = ET.fromstring(data)
    shop = root.find('shop')
    if root.tag != 'yml_catalog' or shop is None:
        raise ValueError('expected yml_catalog/shop')
    source_offers = shop.findall('./offers/offer')
    if not source_offers or len(source_offers) > 15000:
        raise ValueError('feed must contain 1..15000 offers')
    result = ET.Element('yml_catalog', date=datetime.now().strftime('%Y-%m-%d %H:%M'))
    target = ET.SubElement(result, 'shop')
    for key in ('name', 'company', 'url'):
        value = shop.findtext(key, '').strip()
        if not value:
            raise ValueError('missing shop/' + key)
        ET.SubElement(target, key).text = value
    currencies = ET.SubElement(target, 'currencies')
    ET.SubElement(currencies, 'currency', id='RUB', rate='1')
    categories = shop.find('categories')
    if categories is None:
        raise ValueError('missing categories')
    target.append(ET.fromstring(ET.tostring(categories)))
    category_ids = {c.get('id') for c in categories}
    offers = ET.SubElement(target, 'offers')
    seen = set()
    stats = Counter()
    for old in source_offers:
        oid = old.get('id', '').strip()
        try:
            if not oid or oid in seen:
                raise ValueError('empty or duplicate offer id')
            seen.add(oid)
            name = plain(old.findtext('name'))
            if not name:
                name = ' '.join(filter(None, (plain(old.findtext(k)) for k in ('typePrefix', 'vendor', 'model'))))
            if not name:
                raise ValueError('missing name')
            currency = old.findtext('currencyId', '').strip()
            if currency not in ('RUR', 'RUB'):
                raise ValueError('only RUB/RUR supported; no implicit currency conversion')
            price = money(old.findtext('price'))
            category = old.findtext('categoryId', '').strip()
            if category not in category_ids:
                raise ValueError('unknown categoryId')
            pictures = list(dict.fromkeys(p.text.strip() for p in old.findall('picture') if p.text and web_url(p.text.strip())))
            if not pictures:
                raise ValueError('missing HTTP(S) picture URL')
            description = plain(old.findtext('description'))
            if not description:
                description = name
                stats['descriptions_filled'] += 1
            lines = []
            for param in old.findall('param'):
                key, value = plain(param.get('name')), plain(param.text)
                if key == 'is_checkout_enabled':
                    stats['service_params_removed'] += 1
                    continue
                if key and value:
                    unit = plain(param.get('unit'))
                    line = f'{key}: {value}' + (f' {unit}' if unit else '')
                    if line not in lines:
                        lines.append(line)
                    stats['params_moved_to_description'] += 1
            if lines:
                description += '\n\n' + '\n'.join(lines)
            available = old.get('available')
            if available not in (None, 'true', 'false'):
                raise ValueError('invalid available attribute')
            attrs = {'id': oid}
            if available is not None:
                attrs['available'] = available
            offer = ET.SubElement(offers, 'offer', attrs)
            url = old.findtext('url', '').strip()
            if url:
                if not web_url(url):
                    raise ValueError('invalid product URL')
                ET.SubElement(offer, 'url').text = url
            ET.SubElement(offer, 'price').text = price
            oldprice = old.findtext('oldprice')
            if oldprice:
                prior = money(oldprice)
                if int(prior) > int(price):
                    ET.SubElement(offer, 'oldprice').text = prior
            ET.SubElement(offer, 'currencyId').text = 'RUB'
            ET.SubElement(offer, 'categoryId').text = category
            for picture in pictures:
                ET.SubElement(offer, 'picture').text = picture
            vendor = plain(old.findtext('vendor'))
            if vendor:
                ET.SubElement(offer, 'vendor').text = vendor
            ET.SubElement(offer, 'name').text = name
            ET.SubElement(offer, 'description').text = description
        except ValueError as exc:
            raise ValueError(f'offer {oid!r}: {exc}') from exc
    output = ET.tostring(result, encoding='utf-8', xml_declaration=True)
    if len(output) > MAX_BYTES:
        raise ValueError(f'output exceeds {MAX_BYTES} bytes; split the catalog explicitly')
    ET.fromstring(output)
    return output, {'offers': len(source_offers), 'bytes': len(output), **stats}


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
            name = file.name
            file.write(data)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=SOURCE, help='HTTP(S) URL or local XML file')
    parser.add_argument('--output', default='feed-vk.yml')
    parser.add_argument('--no-proxy', action='store_true', help='connect directly, ignoring proxy environment')
    args = parser.parse_args()
    try:
        if web_url(args.source):
            opener = build_opener(ProxyHandler({})) if args.no_proxy else build_opener()
            with opener.open(Request(args.source, headers={'User-Agent': 'VKCommunityFeed/1.0'}), timeout=60) as response:
                data = response.read(32_000_001)
            if len(data) > 32_000_000:
                raise ValueError('source exceeds 32 MB safety limit')
        else:
            data = Path(args.source).read_bytes()
        output, report = convert(data)
        atomic_write(args.output, output)
        print(json.dumps(report, ensure_ascii=True, indent=2))
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
