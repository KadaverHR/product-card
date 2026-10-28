"""Product card HTTP API. Run: python -m uvicorn api:app --port 8000."""
import hmac
import io
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from http.client import HTTPException as HTTPClientError
from http.client import HTTPConnection
from pathlib import Path
from threading import BoundedSemaphore
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Literal
from urllib.parse import quote, urljoin, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from banner_card import compose
from build import ROOT, cutout
from card_output import save_card
from tyre_sizes import parse_size
from truck_card import compose_truck
from truck_sizes import split_truck_size
from dotenv import load_dotenv
import ozon

# Explicit server environment takes priority. No credentials are sent to the UI.
if os.getenv('CARD_SKIP_DOTENV') != '1':
    load_dotenv(ROOT/'.env', override=False)

Theme = Literal['dark', 'light']
ProductType = Literal['passenger', 'truck']
SKU_PATTERN = r'^[A-Za-z0-9_-]{1,100}$'
logger = logging.getLogger(__name__)


def validate_sku(value):
    if not re.fullmatch(SKU_PATTERN, value) or value.upper() in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9]', value, re.I):
        raise ValueError('Артикул: 1–100 латинских букв, цифр, дефисов или подчёркиваний; служебные имена Windows запрещены')
    return value


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    theme: Theme
    product_type: ProductType = 'passenger'
    sku: str = Field(pattern=SKU_PATTERN, description='Артикул товара, например S207352')
    brand: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    size: str = Field(min_length=1, max_length=180, description='Размер шины; для truck допускается свободная запись')
    load_index: str | None = Field(default=None, pattern=r'^\d{2,3}(?:/\d{2,3})?$')
    speed_index: str | None = Field(default=None, pattern=r'^[A-Za-z]{1,3}$')
    image_url: HttpUrl
    season: Literal['summer', 'winter', 'all-season'] | None = None
    application: str | None = Field(default=None, max_length=160)
    ply_rating: str | None = Field(default=None, max_length=80)
    construction: str | None = Field(default=None, max_length=120)
    equipment: str | None = Field(default=None, max_length=120)
    diameter: str | None = Field(default=None, max_length=80)

    @field_validator('sku')
    @classmethod
    def valid_sku(cls, value):
        return validate_sku(value)

    @model_validator(mode='after')
    def validate_product(self):
        if self.product_type == 'truck':
            if self.theme != 'dark':
                raise ValueError('Для грузовых карточек пока доступна только тёмная тема (dark)')
            # Preserve the original free-form truck size; explicit diameter stays separate.
            split_truck_size(self.size, self.diameter)
        else:
            self.size = parse_size(self.size).normalized
            if not self.load_index or not self.speed_index:
                raise ValueError('Для легковых шин обязательны load_index и speed_index')
        return self

    def card_data(self):
        if self.product_type == 'truck':
            return self.model_dump(mode='json', exclude_none=True)
        parsed = parse_size(self.size)
        return {'brand': self.brand, 'model': self.model, 'size': parsed.size,
                'diameter': parsed.diameter, 'load': self.load_index,
                'speed': self.speed_index.upper(), 'season_label': {
                    'summer': 'ЛЕТНИЕ ШИНЫ', 'winter': 'ЗИМНИЕ ШИНЫ',
                    'all-season': 'ВСЕСЕЗОННЫЕ ШИНЫ'}.get(self.season, 'ШИНЫ')}


class ViewerRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['card', 'parameters', 'generate', 'ozon_upload']
    sku: str = Field(pattern=SKU_PATTERN)
    theme: Theme
    product_type: ProductType = 'passenger'
    payload: GenerateRequest | None = None
    background: bool = False


class CardResponse(BaseModel):
    sku: str
    theme: Theme
    image_url: str
    product_type: ProductType = 'passenger'


class Pagination(BaseModel):
    page: int
    per_page: int
    total_pages: int
    has_next_page: bool


class CardList(BaseModel):
    theme: Theme
    product_type: ProductType = 'passenger'
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
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
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
    except (OSError, ValueError, HTTPClientError) as error:
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
        # Flood-fill runs on the working card resolution, not a 20 MP original.
        # This bounds CPU/RAM and avoids proxy timeouts on large supplier photos.
        with Image.open(io.BytesIO(content)) as original:
            original.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            original.save(source, format='PNG')
        try:
            tyre = cutout(source)
        except ValueError:
            fail(422, 'empty_foreground', 'Не удалось выделить товар на фото')
        if data.product_type == 'truck':
            card = compose_truck(tyre, data.card_data())
        else:
            background = ROOT/'assets'/('dark-paint-background-v7.png' if data.theme == 'dark' else 'light-paint-background-v8.png')
            card = compose(tyre, data.card_data(), background, data.theme)
        save_card(card, destination)


def create_app(output=None, public_base_url=None, api_key=None):
    ozon.configure_debug()
    output = Path(output or os.getenv('CARD_OUTPUT_DIR', str(ROOT/'feed-output'))).resolve()
    base_url = (public_base_url or os.getenv('CARD_PUBLIC_BASE_URL', 'https://tools.3sta.ru')).rstrip('/')
    api_key = api_key if api_key is not None else os.getenv('CARD_API_KEY', '')
    for theme in ('dark', 'light'):
        (output/theme).mkdir(parents=True, exist_ok=True)
        (output/'truck'/theme).mkdir(parents=True, exist_ok=True)
    @asynccontextmanager
    async def lifespan(app):
        yield
        executor.shutdown(wait=True)
    app = FastAPI(title='Product Card API', version='1.2.0', lifespan=lifespan, description='Легковые (passenger) и грузовые (truck) карточки. Для грузовых пока доступна генерация только dark.')
    slots = BoundedSemaphore(2)
    ozon_slot = BoundedSemaphore(1)
    job_slots = BoundedSemaphore(3)
    executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix='card-job')
    jobs = {}
    jobs_lock = Lock()

    def start_job(operation):
        if not job_slots.acquire(blocking=False):
            fail(429, 'generator_busy', 'Все задачи заняты. Повторите позже.')
        job_id = uuid.uuid4().hex
        with jobs_lock:
            expired = [key for key, value in jobs.items() if time.monotonic()-value['created'] > 3600 and value['status'] != 'running']
            for key in expired:
                del jobs[key]
            jobs[job_id] = {'created': time.monotonic(), 'status': 'running'}
        def work():
            try:
                result = operation()
                if isinstance(result, BaseModel):
                    result = result.model_dump(mode='json')
                state = {'status': 'completed', 'result': result}
            except HTTPException as error:
                state = {'status': 'failed', 'http_status': error.status_code, 'detail': error.detail}
            except Exception:
                logger.exception('Background card job failed')
                state = {'status': 'failed', 'http_status': 500,
                         'detail': {'message': 'Ошибка обработки. Проверьте журнал API; перед повторной загрузкой проверьте карточку Ozon.'}}
            finally:
                job_slots.release()
            with jobs_lock:
                jobs[job_id].update(state)
        executor.submit(work)
        return {'status': 'running', 'job_id': job_id}
    key_header = APIKeyHeader(name='X-API-Key', auto_error=False)

    def authorize(key: Annotated[str | None, Depends(key_header)]):
        if not api_key:
            fail(503, 'api_key_not_configured', 'API key is not configured')
        if key is None or not hmac.compare_digest(key.encode(), api_key.encode()):
            fail(401, 'unauthorized', 'Invalid or missing X-API-Key')

    def image_folder(theme, product_type='passenger'):
        return output/theme if product_type == 'passenger' else output/'truck'/theme

    def sku_path(sku, theme, extension='.jpg', product_type='passenger'):
        try:
            validate_sku(sku)
        except ValueError as error:
            fail(422, 'invalid_sku', str(error))
        return image_folder(theme, product_type)/f'{sku}{extension}'

    def card_response(sku, theme, path, product_type='passenger'):
        prefix = '' if product_type == 'passenger' else 'truck/'
        return CardResponse(sku=sku, theme=theme, product_type=product_type,
                            image_url=f'{base_url}/images/{prefix}{theme}/{quote(sku)}{path.suffix}?v={path.stat().st_mtime_ns}')

    def parameters_path(sku, theme, product_type='passenger'):
        sku_path(sku, theme, product_type=product_type)
        folder = output.with_name(output.name+'-sources')
        if product_type == 'truck':
            folder /= 'truck'
        return folder/sku/f'generation-{theme}.json'

    @app.get('/cards/viewer', include_in_schema=False)
    def viewer(job_id: str | None = None):
        if job_id is not None:
            with jobs_lock:
                job = jobs.get(job_id)
                if job is None:
                    fail(404, 'job_not_found', 'Задача не найдена. API мог перезапуститься; проверьте результат перед повтором.')
                return {key: value for key, value in job.items() if key != 'created'}
        return FileResponse(ROOT/'viewer.html', media_type='text/html', headers={'Cache-Control': 'no-store'})

    @app.get('/api/v1/cards/{sku}/parameters', dependencies=[Depends(authorize)], summary='Сохранённые параметры генерации')
    def get_parameters(sku: str, theme: Annotated[Theme, Query()], product_type: ProductType = 'passenger'):
        saved = parameters_path(sku, theme, product_type)
        source = saved.parent/'product.json'
        try:
            if saved.is_file():
                return json.loads(saved.read_text(encoding='utf-8'))
            if not source.is_file():
                fail(404, 'parameters_not_found', 'Параметры не сохранены; заполните форму вручную')
            product = json.loads(source.read_text(encoding='utf-8'))
            card = product.get('card', {})
            pictures = product.get('pictures', [])
            return {
                'sku': sku, 'theme': theme,
                'brand': card.get('brand', ''), 'model': card.get('model', ''),
                'size': ' '.join(str(card.get(key) or '') for key in ('size', 'diameter')).strip(),
                'load_index': str(card.get('load') or ''), 'speed_index': str(card.get('speed') or ''),
                'image_url': pictures[0] if pictures else '',
                'season': {'ЛЕТНИЕ ШИНЫ': 'summer', 'ЗИМНИЕ ШИНЫ': 'winter',
                           'ВСЕСЕЗОННЫЕ ШИНЫ': 'all-season'}.get(card.get('season_label')),
            }
        except (OSError, ValueError, TypeError, AttributeError):
            fail(500, 'parameters_read_failed', 'Не удалось прочитать сохранённые параметры')

    @app.get('/api/v1/cards', dependencies=[Depends(authorize)], response_model=CardList, summary='Страница готовых карточек выбранной темы')
    def list_cards(
        theme: Annotated[Theme, Query(description='Обязательная тема: dark или light')],
        page: Annotated[int, Query(ge=1, description='Номер страницы, начиная с 1')] = 1,
        per_page: Annotated[int, Query(ge=1, le=500, description='Карточек на странице, максимум 500')] = 100,
        product_type: ProductType = 'passenger',
    ):
        by_sku = {}
        for path in sorted(image_folder(theme, product_type).iterdir()):
            if path.suffix not in ('.png', '.jpg'):
                continue
            if not path.is_file():
                continue
            try:
                validate_sku(path.stem)
            except ValueError:
                continue
            if path.stem not in by_sku or path.suffix == '.jpg':
                by_sku[path.stem] = path
        paths = [by_sku[sku] for sku in sorted(by_sku)]
        total = len(paths)
        start = (page - 1) * per_page
        items = [card_response(path.stem, theme, path, product_type) for path in paths[start:start + per_page]]
        return CardList(
            theme=theme, product_type=product_type, total=total, count=len(items), items=items,
            pagination=Pagination(page=page, per_page=per_page,
                                  total_pages=(total + per_page - 1) // per_page,
                                  has_next_page=start + per_page < total),
        )

    @app.get('/api/v1/cards/{sku}', dependencies=[Depends(authorize)], response_model=CardResponse, summary='Готовая карточка по артикулу и теме', responses={404: {'description': 'Карточка ещё не сгенерирована'}})
    def get_card(sku: str, theme: Annotated[Theme, Query()], product_type: ProductType = 'passenger'):
        path = sku_path(sku, theme, product_type=product_type)
        if not path.is_file():
            path = sku_path(sku, theme, '.png', product_type)
        if not path.is_file():
            fail(404, 'image_not_found', 'Картинка для этого артикула и темы не сгенерирована')
        return card_response(sku, theme, path, product_type)

    @app.post('/api/v1/cards/generate', dependencies=[Depends(authorize)], response_model=CardResponse, summary='Сгенерировать карточку и вернуть ссылку', responses={422: {'description': 'Некорректные поля или изображение'}, 429: {'description': 'Генератор занят'}, 502: {'description': 'Не удалось скачать фото'}})
    def generate_card(data: GenerateRequest):
        destination = sku_path(data.sku, data.theme, product_type=data.product_type)
        if not slots.acquire(blocking=False):
            fail(429, 'generator_busy', 'Генератор занят; повторите запрос позже')
        try:
            render_card(data, destination)
            saved = parameters_path(data.sku, data.theme, data.product_type)
            saved.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=saved.parent, suffix='.tmp', delete=False) as temporary:
                staging = Path(temporary.name)
            try:
                staging.write_text(data.model_dump_json(indent=2, exclude_none=True, exclude_defaults=True), encoding='utf-8')
                os.replace(staging, saved)
            finally:
                staging.unlink(missing_ok=True)
        except HTTPException:
            raise
        except Exception:
            logger.exception('Generation failed for %s', data.sku)
            fail(500, 'generation_failed', 'Ошибка при генерации карточки')
        finally:
            slots.release()
        return card_response(data.sku, data.theme, destination, data.product_type)

    @app.post('/cards/viewer', include_in_schema=False)
    def viewer_action(data: ViewerRequest):
        # Public UI operations share validation and generation with the keyed API.
        sku_path(data.sku, data.theme, product_type=data.product_type)
        if data.action == 'card':
            return get_card(data.sku, data.theme, data.product_type)
        if data.action == 'parameters':
            return get_parameters(data.sku, data.theme, data.product_type)
        if data.action == 'ozon_upload':
            if data.background:
                return start_job(lambda: upload_ozon(data.sku, data.theme, data.product_type))
            return upload_ozon(data.sku, data.theme, data.product_type)
        if data.payload is None or (data.payload.sku, data.payload.theme, data.payload.product_type) != (data.sku, data.theme, data.product_type):
            fail(422, 'invalid_viewer_payload', 'Generation payload must match the selected SKU and theme')
        if data.background:
            return start_job(lambda: generate_card(data.payload))
        return generate_card(data.payload)

    @app.post('/api/v1/cards/{sku}/ozon', dependencies=[Depends(authorize)], summary='Заменить первые два фото в Ozon')
    def upload_ozon(sku: str, theme: Annotated[Theme, Query()], product_type: ProductType = 'passenger'):
        card = get_card(sku, theme, product_type)
        memo = ROOT/'assets'/'tyre-storage-guide.jpg'
        if not memo.is_file():
            fail(503, 'ozon_memo_missing', 'Не найдена вторая картинка — памятка хранения шин.')
        if not ozon_slot.acquire(blocking=False):
            fail(429, 'ozon_busy', 'Идёт загрузка в Ozon. Повторите позже.')
        try:
            product = ozon.product_pictures(sku)
            token = uuid.uuid4().hex
            backup = output.with_name(output.name+'-ozon-backups')/token
            backup.mkdir(parents=True)
            (backup/'before.json').write_text(json.dumps(product, ensure_ascii=False, indent=2), encoding='utf-8')
            # Archive originals before any mutation; retain original URLs after slot 2.
            for index, url in enumerate(product['images']):
                content = download_image(url)
                (backup/f'original-{index+1:02d}.img').write_bytes(content)
            if product['color_image']:
                (backup/'color.img').write_bytes(download_image(product['color_image']))
            publish = output/'ozon-publish'/token
            publish.mkdir(parents=True)
            source = sku_path(sku, theme, Path(urlsplit(card.image_url).path).suffix, product_type)
            # Immutable URLs: another generation must not change an in-flight import.
            import shutil
            if source.suffix == '.jpg':
                shutil.copyfile(source, publish/'card.jpg')
            else:
                with Image.open(source) as image:
                    image.convert('RGB').save(publish/'card.jpg', quality=82, subsampling=2, optimize=True)
            shutil.copyfile(memo, publish/'guide.jpg')
            images = [f'{base_url}/images/ozon/{token}/card.jpg',
                      f'{base_url}/images/ozon/{token}/guide.jpg'] + product['images'][2:]
            (backup/'request.json').write_text(json.dumps({'images': images}, indent=2), encoding='utf-8')
            result = ozon.import_pictures(product, images)
            (backup/'response.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            return {'status': 'submitted', 'offer_id': product['offer_id'],
                    'product_id': product['product_id'], 'images': images,
                    'preserved_count': max(0, len(product['images'])-2),
                    'message': 'Ozon принял запрос. Обработка изображений выполняется на стороне Ozon.'}
        except ozon.OzonError as error:
            fail(error.status, 'ozon_error', str(error))
        except HTTPException:
            raise
        except (OSError, ValueError, HTTPClientError):
            logger.exception('Ozon upload failed for %s', sku)
            fail(502, 'ozon_upload_failed', 'Не удалось сохранить исходные фото или передать запрос. Проверьте карточку Ozon перед повтором.')
        finally:
            ozon_slot.release()

    @app.get('/images/ozon/{token}/{filename}', include_in_schema=False)
    def ozon_published_image(token: str, filename: Literal['card.jpg', 'guide.jpg']):
        if not re.fullmatch(r'[a-f0-9]{32}', token):
            fail(404, 'image_not_found', 'Картинка не найдена')
        path = output/'ozon-publish'/token/filename
        if not path.is_file():
            fail(404, 'image_not_found', 'Картинка не найдена')
        return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'public, max-age=31536000, immutable'})

    @app.get('/images/truck/{theme}/{sku}.{extension}', include_in_schema=False)
    def truck_image_file(theme: Theme, sku: str, extension: Literal['jpg', 'png']):
        path = sku_path(sku, theme, '.'+extension, 'truck')
        if not path.is_file():
            fail(404, 'image_not_found', 'Картинка не найдена')
        return FileResponse(path, media_type='image/jpeg' if extension == 'jpg' else 'image/png',
                            headers={'Cache-Control': 'no-cache'})

    @app.get('/images/{theme}/{sku}.jpg', include_in_schema=False)
    def image_file(theme: Theme, sku: str):
        path = sku_path(sku, theme)
        if not path.is_file():
            fail(404, 'image_not_found', 'Картинка не найдена')
        return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'no-cache'})

    @app.get('/images/{theme}/{sku}.png', include_in_schema=False)
    def legacy_image_file(theme: Theme, sku: str):
        path = sku_path(sku, theme, '.png')
        if not path.is_file():
            fail(404, 'image_not_found', 'Картинка не найдена')
        return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'no-cache'})

    return app


app = create_app()
