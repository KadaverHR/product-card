"""Product card HTTP API. Run: python -m uvicorn api:app --port 8000."""
import hmac
import io
import ipaddress
import logging
import os
import re
import socket
import ssl
import tempfile
import time
from http.client import HTTPConnection
from pathlib import Path
from threading import BoundedSemaphore
from typing import Annotated, Literal
from urllib.parse import quote, urljoin, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from banner_card import compose
from build import ROOT, cutout

Theme = Literal['dark', 'light']
SKU_PATTERN = r'^[A-Za-z0-9_-]{1,100}$'
logger = logging.getLogger(__name__)


def validate_sku(value):
    if not re.fullmatch(SKU_PATTERN, value) or value.upper() in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9]', value, re.I):
        raise ValueError('Артикул: 1–100 латинских букв, цифр, дефисов или подчёркиваний; служебные имена Windows запрещены')
    return value


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    theme: Theme
    sku: str = Field(pattern=SKU_PATTERN, description='Артикул товара, например S207352')
    brand: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    size: str = Field(pattern=r'^\d{3}/\d{2,3}\s+[Rr]\d{2}$', description='Полный размер: 225/65 R17')
    load_index: str = Field(pattern=r'^\d{2,3}(?:/\d{2,3})?$')
    speed_index: str = Field(pattern=r'^[A-Za-z]{1,3}$')
    image_url: HttpUrl
    season: Literal['summer', 'winter', 'all-season'] | None = None

    @field_validator('sku')
    @classmethod
    def valid_sku(cls, value):
        return validate_sku(value)

    def card_data(self):
        size, diameter = self.size.upper().split()
        return {'brand': self.brand, 'model': self.model, 'size': size,
                'diameter': diameter, 'load': self.load_index,
                'speed': self.speed_index.upper(), 'season_label': {
                    'summer': 'ЛЕТНИЕ ШИНЫ', 'winter': 'ЗИМНИЕ ШИНЫ',
                    'all-season': 'ВСЕСЕЗОННЫЕ ШИНЫ'}.get(self.season, 'ШИНЫ')}


class CardResponse(BaseModel):
    sku: str
    theme: Theme
    image_url: str


class Pagination(BaseModel):
    page: int
    per_page: int
    total_pages: int
    has_next_page: bool


class CardList(BaseModel):
    theme: Theme
    total: int
    count: int
    items: list[CardResponse]
    pagination: Pagination


def fail(status, code, message):
    raise HTTPException(status_code=status, detail={'code': code, 'message': message})


def download_image(url, timeout=30, max_bytes=30*1024*1024):
    """Use public addresses only, pin DNS per hop and verify HTTPS certificates."""
    deadline = time.monotonic()+timeout
    for _ in range(6):
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            fail(422, 'invalid_image_url', 'Нужна HTTP/HTTPS ссылка без логина и пароля')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if port not in (80, 443):
            fail(422, 'invalid_image_url', 'Разрешены только порты 80 и 443')
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            fail(422, 'invalid_image_url', 'Адрес изображения должен быть публичным')
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Image download timed out')
        connection = HTTPConnection(parsed.hostname, port, timeout=remaining)
        family, socktype, protocol, _, address = addresses[0]
        sock = socket.socket(family, socktype, protocol)
        try:
            sock.settimeout(remaining)
            sock.connect(address)
            if parsed.scheme == 'https':
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
            connection.sock = sock
            path = parsed.path or '/'
            if parsed.query:
                path += '?'+parsed.query
            connection.request('GET', path, headers={'User-Agent': 'ProductCardAPI/1.0', 'Accept': 'image/*'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    fail(502, 'image_download_failed', 'Перенаправление не содержит ссылки')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                fail(502, 'image_download_failed', f'Сервер изображения ответил HTTP {response.status}')
            content_length = response.getheader('Content-Length')
            if content_length and int(content_length) > max_bytes:
                fail(413, 'image_too_large', 'Изображение больше 30 МБ')
            result = bytearray()
            while True:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Image download timed out')
                chunk = response.read(65536)
                if not chunk:
                    return bytes(result)
                result.extend(chunk)
                if len(result) > max_bytes:
                    fail(413, 'image_too_large', 'Изображение больше 30 МБ')
        finally:
            connection.close()
            sock.close()
    fail(502, 'image_download_failed', 'Слишком много перенаправлений')


def render_card(data, destination):
    try:
        content = download_image(str(data.image_url))
    except HTTPException:
        raise
    except (OSError, ValueError) as error:
        logger.warning('Download failed for %s: %s', data.sku, type(error).__name__)
        fail(502, 'image_download_failed', 'Не удалось скачать исходное изображение')
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.width*image.height > 20_000_000:
                fail(413, 'image_too_large', 'Изображение больше 20 мегапикселей')
            if image.format not in ('JPEG', 'PNG', 'WEBP'):
                fail(422, 'invalid_image', 'Поддерживаются JPEG, PNG и WebP')
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        fail(422, 'invalid_image', 'Ссылка не содержит исправного JPEG, PNG или WebP')
    with tempfile.TemporaryDirectory(prefix='card-') as directory:
        source = Path(directory)/'source.img'
        source.write_bytes(content)
        try:
            tyre = cutout(source)
        except ValueError:
            fail(422, 'empty_foreground', 'Не удалось выделить товар на фото')
        background = ROOT/'assets'/('dark-paint-background-v7.png' if data.theme == 'dark' else 'paint-background-v4.png')
        card = compose(tyre, data.card_data(), background, data.theme)
        # Save beside the final file, then publish atomically on the same volume.
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.card-', suffix='.tmp', delete=False) as temporary:
            staging = Path(temporary.name)
        try:
            card.save(staging, format='PNG')
            os.replace(staging, destination)
        finally:
            staging.unlink(missing_ok=True)


def create_app(output=None, public_base_url=None, api_key=None):
    output = Path(output or os.getenv('CARD_OUTPUT_DIR', str(ROOT/'feed-output'))).resolve()
    base_url = (public_base_url or os.getenv('CARD_PUBLIC_BASE_URL', 'https://tools.3sta.ru')).rstrip('/')
    api_key = api_key if api_key is not None else os.getenv('CARD_API_KEY', '')
    for theme in ('dark', 'light'):
        (output/theme).mkdir(parents=True, exist_ok=True)
    app = FastAPI(title='Product Card API', version='1.0.0', description='Готовые карточки и генерация изображений по артикулу. Темы: dark, light.')
    key_header = APIKeyHeader(name='X-API-Key', auto_error=False)
    slots = BoundedSemaphore(2)

    def authorize(key: Annotated[str | None, Depends(key_header)]):
        if api_key and (key is None or not hmac.compare_digest(key.encode(), api_key.encode())):
            fail(401, 'unauthorized', 'Неверный или отсутствующий X-API-Key')

    def sku_path(sku, theme):
        try:
            validate_sku(sku)
        except ValueError as error:
            fail(422, 'invalid_sku', str(error))
        return output/theme/f'{sku}.png'

    def card_response(sku, theme, path):
        return CardResponse(sku=sku, theme=theme, image_url=f'{base_url}/images/{theme}/{quote(sku)}.png?v={path.stat().st_mtime_ns}')

    @app.get('/api/v1/cards', response_model=CardList, dependencies=[Depends(authorize)], summary='Страница готовых карточек выбранной темы')
    def list_cards(
        theme: Annotated[Theme, Query(description='Обязательная тема: dark или light')],
        page: Annotated[int, Query(ge=1, description='Номер страницы, начиная с 1')] = 1,
        per_page: Annotated[int, Query(ge=1, le=500, description='Карточек на странице, максимум 500')] = 100,
    ):
        paths = []
        for path in sorted((output/theme).glob('*.png')):
            if not path.is_file():
                continue
            try:
                validate_sku(path.stem)
            except ValueError:
                continue
            paths.append(path)
        total = len(paths)
        start = (page - 1) * per_page
        items = [card_response(path.stem, theme, path) for path in paths[start:start + per_page]]
        return CardList(
            theme=theme, total=total, count=len(items), items=items,
            pagination=Pagination(page=page, per_page=per_page,
                                  total_pages=(total + per_page - 1) // per_page,
                                  has_next_page=start + per_page < total),
        )

    @app.get('/api/v1/cards/{sku}', response_model=CardResponse, dependencies=[Depends(authorize)], summary='Готовая карточка по артикулу и теме', responses={404: {'description': 'Карточка ещё не сгенерирована'}})
    def get_card(sku: str, theme: Annotated[Theme, Query()]):
        path = sku_path(sku, theme)
        if not path.is_file():
            fail(404, 'image_not_found', 'Картинка для этого артикула и темы не сгенерирована')
        return card_response(sku, theme, path)

    @app.post('/api/v1/cards/generate', response_model=CardResponse, dependencies=[Depends(authorize)], summary='Сгенерировать карточку и вернуть ссылку', responses={422: {'description': 'Некорректные поля или изображение'}, 429: {'description': 'Генератор занят'}, 502: {'description': 'Не удалось скачать фото'}})
    def generate_card(data: GenerateRequest):
        destination = sku_path(data.sku, data.theme)
        if not slots.acquire(blocking=False):
            fail(429, 'generator_busy', 'Генератор занят; повторите запрос позже')
        try:
            render_card(data, destination)
        except HTTPException:
            raise
        except Exception:
            logger.exception('Generation failed for %s', data.sku)
            fail(500, 'generation_failed', 'Ошибка при генерации карточки')
        finally:
            slots.release()
        return card_response(data.sku, data.theme, destination)

    @app.get('/images/{theme}/{sku}.png', include_in_schema=False)
    def image_file(theme: Theme, sku: str):
        path = sku_path(sku, theme)
        if not path.is_file():
            fail(404, 'image_not_found', 'Картинка не найдена')
        return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'no-cache'})

    return app


app = create_app()
