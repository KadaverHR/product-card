"""Persistent named YML feeds, with atomic publication after validation."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import uuid
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
from typing import Literal

from vk_feed import atomic_write, convert_parts


class FeedRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    source_url: HttpUrl
    format: Literal['vk', 'generation'] = 'vk'
    product_type: Literal['passenger', 'truck', 'agricultural', 'special'] = 'truck'
    theme: Literal['dark', 'light'] = 'dark'
    offer_ids: list[str] = Field(default_factory=list, max_length=15000)
    pictures: Literal['original', 'cards'] = 'original'

    @field_validator('offer_ids')
    @classmethod
    def clean_ids(cls, values):
        if any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError('Артикулы: 1–100 символов; для всего каталога передайте пустой список')
        return list(dict.fromkeys(value.strip() for value in values))


class FeedStore:
    def __init__(self, folder, base_url):
        self.folder = Path(folder)
        self.base_url = base_url

    def directory(self, feed_id):
        if not re.fullmatch(r'[a-f0-9]{32}', feed_id):
            raise FileNotFoundError('Фид не найден')
        return self.folder/feed_id

    def get(self, feed_id):
        path = self.directory(feed_id)/'current.json'
        if not path.is_file():
            raise FileNotFoundError('Фид не найден')
        return json.loads(path.read_text(encoding='utf-8'))

    def list(self):
        if not self.folder.exists():
            return []
        return sorted((self.get(path.parent.name) for path in self.folder.glob('*/current.json')),
                      key=lambda item: item['updated_at'], reverse=True)

    def file(self, feed_id, filename='feed.yml'):
        metadata = self.get(feed_id)
        names = {item['name'] for item in metadata.get('files', [{'name': 'feed.yml'}])}
        if filename not in names:
            raise FileNotFoundError('Часть фида не найдена')
        return self.directory(feed_id)/metadata['revision']/filename

    def delete(self, feed_id):
        self.get(feed_id)
        path = self.directory(feed_id)
        root = self.folder.resolve()
        # Never follow a feed directory symlink or delete outside feed storage.
        if path.is_symlink() or path.resolve().parent != root:
            raise ValueError('Недопустимый путь фида')
        retired = root/('.deleting-'+uuid.uuid4().hex)
        path.rename(retired)
        shutil.rmtree(retired)
        return {'status': 'deleted', 'id': feed_id}

    def build(self, request, data, card_url, feed_id=None):
        if len(data) > 32_000_000:
            raise ValueError('Исходный фид больше 32 МБ')
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
            raise ValueError('DTD и XML entities не поддерживаются')
        root = ET.fromstring(data)
        offers = root.find('./shop/offers')
        if root.tag != 'yml_catalog' or offers is None:
            raise ValueError('Ожидается YML: yml_catalog/shop/offers')
        requested = {value.strip() for value in request.offer_ids if value.strip()}
        found, seen = set(), set()
        replaced = 0
        for offer in list(offers):
            if offer.tag != 'offer':
                continue
            sku = (offer.get('id') or '').strip()
            if requested and sku not in requested:
                offers.remove(offer)
                continue
            if not sku or sku in seen:
                raise ValueError(f'Пустой или повторяющийся артикул: {sku!r}')
            found.add(sku)
            seen.add(sku)
            # A published subset must not depend on relative supplier URLs.
            for element in list(offer):
                if element.tag in ('picture', 'url') and element.text:
                    element.text = urljoin(str(request.source_url), element.text.strip())
            if request.pictures == 'cards':
                url = card_url(sku, request.theme, request.product_type)
                pictures = offer.findall('picture')
                if pictures:
                    pictures[0].text = url
                else:
                    picture = ET.Element('picture')
                    picture.text = url
                    offer.insert(0, picture)
                replaced += 1
        if requested-found:
            missing = sorted(requested-found)
            raise ValueError('Не найдены артикулы: '+', '.join(missing[:20]))
        if not found:
            raise ValueError('Нет товаров для публикации')
        output = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        if request.format == 'vk':
            parts, report = convert_parts(output)
        else:
            parts = [(output, len(found))]
            report = {'offers': len(found), 'bytes': len(output), 'parts': 1}
        report['pictures_replaced'] = replaced
        feed_id = feed_id or uuid.uuid4().hex
        previous = self.get(feed_id) if (self.directory(feed_id)/'current.json').is_file() else None
        revision = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        metadata = {'id': feed_id, 'revision': revision, 'created_at': previous['created_at'] if previous else now,
                    'updated_at': now, 'config': request.model_dump(mode='json'), 'report': report,
                    'url': f'{self.base_url}/cards/feeds/files/{feed_id}/feed.yml'}
        metadata['files'] = []
        # Publish the pointer last: failed conversions leave the previous URL intact.
        for index, (content, count) in enumerate(parts, 1):
            name = 'feed.yml' if index == 1 else f'part-{index:03d}.yml'
            atomic_write(self.directory(feed_id)/revision/name, content)
            metadata['files'].append({'name': name, 'offers': count, 'bytes': len(content),
                                      'url': f'{self.base_url}/cards/feeds/files/{feed_id}/{name}'})
        atomic_write(self.directory(feed_id)/'current.json',
                     json.dumps(metadata, ensure_ascii=False, indent=2).encode('utf-8'))
        return metadata
