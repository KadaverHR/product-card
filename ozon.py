"""Ozon Seller API integration. Credentials stay on the server."""
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger('product_card.ozon')


class SecretFormatter(logging.Formatter):
    def format(self, record):
        text = super().format(record)
        for name in ('OZON_API_KEY', 'OZON_CLIENT_ID', 'CARD_API_KEY'):
            secret = os.getenv(name, '')
            if secret:
                text = text.replace(secret, '[HIDDEN]')
        return text


def configure_debug():
    if os.getenv('OZON_DEBUG', '').lower() not in ('1', 'true', 'yes'):
        return
    if any(getattr(handler, 'ozon_server_debug', False) for handler in logger.handlers):
        return
    path = Path(__file__).resolve().parent/'debug-ozon.log'
    handler = RotatingFileHandler(path, maxBytes=10*1024*1024, backupCount=2, encoding='utf-8')
    handler.ozon_server_debug = True
    handler.setFormatter(SecretFormatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.debug('OZON DEBUG ENABLED')


class OzonError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def request(path, payload):
    client_id = os.getenv('OZON_CLIENT_ID', '').strip()
    api_key = os.getenv('OZON_API_KEY', '').strip()
    if not client_id or not api_key:
        logger.debug('OZON CONFIG ERROR: credentials missing (values are not logged)')
        raise OzonError('Заполните OZON_CLIENT_ID и OZON_API_KEY на сервере и перезапустите API.', 503)
    req = Request('https://api-seller.ozon.ru'+path,
                  data=json.dumps(payload).encode('utf-8'), method='POST',
                  headers={'Client-Id': client_id, 'Api-Key': api_key,
                           'Content-Type': 'application/json'})
    logger.debug('OZON REQUEST POST %s\n%s', path, json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        with urlopen(req, timeout=30) as response:
            body = response.read().decode('utf-8', errors='replace')
            logger.debug('OZON RESPONSE %s HTTP %s\n%s', path, response.status, body)
            data = json.loads(body)
    except HTTPError as error:
        try:
            logger.debug('OZON RESPONSE %s HTTP %s\n%s', path, error.code,
                         error.read().decode('utf-8', errors='replace'))
        finally:
            error.close()
        # Never expose response headers, credentials or arbitrary upstream content.
        raise OzonError(f'Ozon вернул HTTP {error.code}. Проверьте права ключа и журнал загрузки.') from None
    except (URLError, TimeoutError, OSError) as error:
        logger.debug('OZON NETWORK ERROR %s: %s', path, type(error).__name__)
        raise OzonError('Нет ответа Ozon. Если выполнялась отправка, проверьте карточку в кабинете перед повтором.') from None
    except (ValueError, TypeError):
        raise OzonError('Ozon вернул некорректный ответ.') from None
    if not isinstance(data, dict):
        raise OzonError('Ozon вернул неожиданный формат ответа.')
    return data


def product_pictures(sku):
    offer_id = sku+'SU'
    products = request('/v3/product/info/list', {'offer_id': [offer_id]})
    items = products.get('items')
    if not isinstance(items, list):
        raise OzonError('Не удалось прочитать список товаров Ozon.')
    matches = [item for item in items if item.get('offer_id') == offer_id]
    if not matches:
        raise OzonError(f'Товар {offer_id} не найден в Ozon.', 404)
    if len(matches) != 1 or not matches[0].get('id'):
        raise OzonError('Неоднозначный ответ Ozon: загрузка отменена.')
    product_id = matches[0]['id']
    response = request('/v2/product/pictures/info', {'product_id': [str(product_id)]})
    items = response.get('items')
    if not isinstance(items, list):
        raise OzonError('Не удалось прочитать фотографии Ozon.')
    matches = [item for item in items if str(item.get('product_id')) == str(product_id)]
    if len(matches) != 1:
        raise OzonError('Неоднозначный ответ Ozon по фотографиям: загрузка отменена.')
    item = matches[0]
    # URL-specific failures may refer to earlier imports, not the current gallery.
    # Validate/archive the current URLs below instead of blocking their replacement.
    errors = item.get('errors') or []
    if not isinstance(errors, list) or any(
        not isinstance(error, dict) or not isinstance(error.get('url'), str) or not error['url'].strip()
        for error in errors
    ):
        raise OzonError('Ozon вернул ошибку фотографий без ссылки: загрузка отменена.')
    if errors:
        logger.warning('OZON PHOTO WARNINGS product_id=%s count=%s; checking current gallery before import', product_id, len(errors))
    def links(field):
        values = item.get(field, [])
        if not isinstance(values, list) or any(not isinstance(url, str) or not url.startswith(('https://', 'http://')) for url in values):
            raise OzonError('Некорректный список фотографий Ozon: загрузка отменена.')
        return values
    primary, photos, colors = links('primary_photo'), links('photo'), links('color_photo')
    if len(primary) > 1 or len(colors) > 1:
        raise OzonError('Неоднозначный порядок фотографий Ozon: загрузка отменена.')
    # photo contains additional photos; some responses also repeat primary there.
    images = primary + [url for url in photos if url not in primary]
    if len(images) > 30:
        raise OzonError('Слишком много изображений Ozon: загрузка отменена.')
    return {'offer_id': offer_id, 'product_id': product_id,
            'images': images, 'color_image': colors[0] if colors else '',
            'previous_errors': errors}


def import_pictures(product, images):
    payload = {'product_id': int(product['product_id']), 'images': images,
               'color_image': product['color_image']}
    result = request('/v1/product/pictures/import', payload)
    pictures = result.get('result', {}).get('pictures')
    if not isinstance(pictures, list) or not pictures:
        raise OzonError('Ozon не подтвердил приём изображений. Проверьте карточку в кабинете перед повтором.')
    if any(p.get('state') not in ('imported', 'uploaded', 'pending') for p in pictures):
        raise OzonError('Ozon сообщил об ошибке импорта. Проверьте карточку в кабинете.')
    return result
