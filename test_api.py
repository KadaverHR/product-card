"""API contract tests without external downloads or real card generation."""
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from api import create_app, download_image, render_card, GenerateRequest


class CardAPITest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.addCleanup(lambda: shutil.rmtree(self.output.with_name(self.output.name+'-sources'), ignore_errors=True))
        self.client = TestClient(create_app(self.output, 'https://cards.example.com', 'secret'))
        self.headers = {'X-API-Key': 'secret'}
        self.payload = dict(theme='dark', sku='S207352', brand='Cordiant', model='GRAVITY SUV',
                            size='225/65 R17', load_index='106', speed_index='H',
                            image_url='https://example.com/photo.jpg', season='summer')

    def test_theme_is_required_and_api_is_protected(self):
        self.assertEqual(self.client.get('/api/v1/cards?theme=dark').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/cards?theme=dark', headers={'X-API-Key': 'wrong'}).status_code, 401)
        self.assertEqual(self.client.get('/api/v1/cards', headers=self.headers).status_code, 422)
        self.assertEqual(self.client.get('/api/v1/cards?theme=purple', headers=self.headers).status_code, 422)

    def test_list_filters_theme_and_lookup_does_not_generate(self):
        (self.output/'dark/S207352.png').write_bytes(b'PNG-test')
        (self.output/'light/OTHER.png').write_bytes(b'light')
        response = self.client.get('/api/v1/cards?theme=dark', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(response.json()['items'][0]['sku'], 'S207352')
        self.assertTrue(response.json()['items'][0]['image_url'].startswith('https://cards.example.com/images/dark/S207352.png'))
        self.assertEqual(self.client.get('/api/v1/cards/S207352?theme=dark', headers=self.headers).status_code, 200)
        missing = self.client.get('/api/v1/cards/S207352?theme=light', headers=self.headers)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()['detail']['code'], 'image_not_found')
        # Image links work without API credentials.
        self.assertEqual(self.client.get('/images/dark/S207352.png').content, b'PNG-test')

    def test_jpeg_preferred_without_duplicates_and_legacy_png_still_public(self):
        (self.output/'dark/S207352.png').write_bytes(b'old-png')
        (self.output/'dark/S207352.jpg').write_bytes(b'new-jpg')
        (self.output/'dark/PNG_ONLY.png').write_bytes(b'png')
        (self.output/'dark/JPG_ONLY.jpg').write_bytes(b'jpg')
        body = self.client.get('/api/v1/cards?theme=dark', headers=self.headers).json()
        self.assertEqual(body['total'], 3)
        self.assertEqual([item['sku'] for item in body['items']], ['JPG_ONLY', 'PNG_ONLY', 'S207352'])
        self.assertIn('/S207352.jpg?', body['items'][2]['image_url'])
        card = self.client.get('/api/v1/cards/S207352?theme=dark', headers=self.headers).json()
        self.assertIn('/S207352.jpg?', card['image_url'])
        jpg = self.client.get('/images/dark/S207352.jpg')
        self.assertEqual(jpg.content, b'new-jpg')
        self.assertEqual(jpg.headers['content-type'], 'image/jpeg')
        png = self.client.get('/images/dark/S207352.png')
        self.assertEqual(png.content, b'old-png')
        self.assertEqual(png.headers['content-type'], 'image/png')
        self.assertEqual(self.client.get('/images/dark/MISSING.jpg').status_code, 404)
        self.assertEqual(self.client.get('/images/dark/CON.jpg').status_code, 422)

    def test_conversion_keeps_png_and_existing_jpeg(self):
        from convert_cards import convert_cards
        from contextlib import redirect_stdout
        original = self.output/'dark/S207352.png'
        Image.new('RGB', (100, 100), 'white').save(original)
        initial = original.read_bytes()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(convert_cards(self.output), 0)
        self.assertEqual(original.read_bytes(), initial)
        destination = original.with_suffix('.jpg')
        with Image.open(destination) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(image.size, (100, 100))
        destination.write_bytes(b'keep-existing')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(convert_cards(self.output), 0)
        self.assertEqual(destination.read_bytes(), b'keep-existing')

    def test_commercial_diameters_and_optional_profile(self):
        from pydantic import ValidationError
        for supplied, normalized, size, diameter in [
                ('195/75 R16C', '195/75 R16C', '195/75', 'R16C'),
                ('195/75r16c', '195/75 R16C', '195/75', 'R16C'),
                ('195R14', '195 R14', '195', 'R14'),
                ('195 R14C', '195 R14C', '195', 'R14C'),
                ('225/65 R17', '225/65 R17', '225/65', 'R17')]:
            with self.subTest(size=supplied):
                payload = dict(self.payload, size=supplied)
                data = GenerateRequest(**payload)
                self.assertEqual(data.size, normalized)
                self.assertEqual(data.card_data()['size'], size)
                self.assertEqual(data.card_data()['diameter'], diameter)
                with patch('api.render_card', side_effect=lambda data, path: path.write_bytes(b'card')):
                    self.assertEqual(self.client.post('/api/v1/cards/generate', json=payload, headers=self.headers).status_code, 200)
                    request = dict(action='generate', sku=payload['sku'], theme=payload['theme'], payload=payload)
                    self.assertEqual(self.client.post('/cards/viewer', json=request).status_code, 200)
                saved = self.client.get('/api/v1/cards/S207352/parameters?theme=dark', headers=self.headers).json()
                self.assertEqual(saved['size'], normalized)
        for size in ('195', 'R16C', '195/ R16C', '195 R16CC', '195 R160C', '195 R16X', '195 R16C 106H'):
            with self.subTest(invalid=size), self.assertRaises(ValidationError):
                GenerateRequest(**dict(self.payload, size=size))

    def test_generate_returns_link_and_publishes_to_correct_theme(self):
        def fake_render(data, path):
            self.assertEqual(data.card_data()['diameter'], 'R17')
            self.assertEqual(data.card_data()['season_label'], '\u041b\u0415\u0422\u041d\u0418\u0415 \u0428\u0418\u041d\u042b')
            path.write_bytes(b'new-card')
        with patch('api.render_card', side_effect=fake_render):
            response = self.client.post('/api/v1/cards/generate', json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sku'], 'S207352')
        self.assertTrue((self.output/'dark/S207352.jpg').is_file())
        self.assertFalse((self.output/'light/S207352.jpg').exists())
        saved = self.client.get('/api/v1/cards/S207352/parameters?theme=dark', headers=self.headers)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), self.payload)

    def test_viewer_reads_feed_parameters_and_enforces_api_authorization(self):
        folder = self.output.with_name(self.output.name+'-sources')/'S207352'
        folder.mkdir(parents=True)
        product = {'card': {'brand': 'Cordiant', 'model': 'GRAVITY SUV', 'size': '225/65',
                           'diameter': 'R17', 'load': '106', 'speed': 'H', 'season_label': 'ЛЕТНИЕ ШИНЫ'},
                   'pictures': ['https://example.com/photo.jpg']}
        (folder/'product.json').write_text(json.dumps(product), encoding='utf-8')
        path = '/api/v1/cards/S207352/parameters?theme=dark'
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers=self.headers).json(), self.payload)
        self.assertEqual(self.client.get('/cards/viewer').status_code, 200)
        self.assertEqual(self.client.post('/cards/viewer', json={'action': 'parameters', 'sku': 'S207352', 'theme': 'dark'}).json(), self.payload)
        self.assertEqual(self.client.get('/api/v1/cards/MISSING/parameters?theme=light', headers=self.headers).status_code, 404)

    def test_public_viewer_generates_without_exposing_api_key(self):
        request = dict(action='generate', sku='S207352', theme='dark', payload=self.payload)
        with patch('api.render_card', side_effect=lambda data, path: path.write_bytes(b'card')) as render:
            self.assertEqual(self.client.post('/api/v1/cards/generate', json=self.payload).status_code, 401)
            render.assert_not_called()
            self.assertEqual(self.client.post('/cards/viewer', json=request).status_code, 200)
            render.assert_called_once()
        card = self.client.post('/cards/viewer', json=dict(action='card', sku='S207352', theme='dark'))
        self.assertEqual(card.status_code, 200)
        self.assertEqual(card.json()['sku'], 'S207352')
        self.assertEqual(self.client.get('/api/v1/cards/S207352?theme=dark').status_code, 401)
        request['sku'] = 'OTHER'
        self.assertEqual(self.client.post('/cards/viewer', json=request).status_code, 422)
        request.pop('payload')
        self.assertEqual(self.client.post('/cards/viewer', json=request).status_code, 422)
        page = self.client.get('/cards/viewer').text
        self.assertNotIn('X-API-Key', page)
        self.assertNotIn('/api/v1/cards', page)

    def test_api_without_configured_key_fails_closed(self):
        client = TestClient(create_app(self.output, 'https://cards.example.com', ''))
        self.assertEqual(client.get('/api/v1/cards?theme=dark').status_code, 503)
        self.assertEqual(client.get('/cards/viewer').status_code, 200)

    def test_regeneration_parameters_are_separate_for_each_theme(self):
        folder = self.output.with_name(self.output.name+'-sources')
        with patch('api.render_card', side_effect=lambda data, path: path.write_bytes(b'card')):
            for theme in ('dark', 'light'):
                payload = dict(self.payload, theme=theme, model=theme+' model')
                self.assertEqual(self.client.post('/api/v1/cards/generate', json=payload, headers=self.headers).status_code, 200)
        for theme in ('dark', 'light'):
            result = self.client.get('/api/v1/cards/S207352/parameters?theme='+theme, headers=self.headers).json()
            self.assertEqual(result['model'], theme+' model')

    def test_pagination_sorts_and_counts_valid_cards_only(self):
        for sku in ('C', 'A', 'B', 'CON'):
            (self.output/f'dark/{sku}.png').write_bytes(b'card')
        (self.output/'dark/folder.png').mkdir()
        (self.output/'light/OTHER.png').write_bytes(b'card')
        response = self.client.get('/api/v1/cards?theme=dark&page=2&per_page=2', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([item['sku'] for item in body['items']], ['C'])
        self.assertEqual(body['total'], 3)
        self.assertEqual(body['count'], 1)
        self.assertEqual(body['pagination'], {'page': 2, 'per_page': 2, 'total_pages': 2, 'has_next_page': False})
        first = self.client.get('/api/v1/cards?theme=dark&page=1&per_page=2', headers=self.headers).json()
        self.assertEqual([item['sku'] for item in first['items']], ['A', 'B'])
        self.assertTrue(first['pagination']['has_next_page'])
        empty = self.client.get('/api/v1/cards?theme=dark&page=3&per_page=2', headers=self.headers).json()
        self.assertEqual(empty['items'], [])
        self.assertEqual(empty['total'], 3)
        self.assertEqual(empty['count'], 0)

    def test_pagination_defaults_and_invalid_parameters(self):
        body = self.client.get('/api/v1/cards?theme=dark', headers=self.headers).json()
        self.assertEqual(body['pagination'], {'page': 1, 'per_page': 100, 'total_pages': 0, 'has_next_page': False})
        for query in ('page=0', 'page=-1', 'page=abc', 'per_page=0', 'per_page=501', 'per_page=abc'):
            self.assertEqual(self.client.get('/api/v1/cards?theme=dark&'+query, headers=self.headers).status_code, 422, query)

    def test_validation_and_generation_failure_preserve_old_card(self):
        for field in ('theme', 'sku', 'brand', 'model', 'size', 'load_index', 'speed_index', 'image_url'):
            payload = self.payload.copy()
            payload.pop(field)
            self.assertEqual(self.client.post('/api/v1/cards/generate', json=payload, headers=self.headers).status_code, 422, field)
        for bad_sku in ('../outside', 'CON'):
            payload = dict(self.payload, sku=bad_sku)
            self.assertEqual(self.client.post('/api/v1/cards/generate', json=payload, headers=self.headers).status_code, 422)
        path = self.output/'dark/S207352.png'
        path.write_bytes(b'previous')
        with patch('api.render_card', side_effect=RuntimeError('test failure')), self.assertLogs('api', level='ERROR'):
            response = self.client.post('/api/v1/cards/generate', json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(path.read_bytes(), b'previous')

    def test_atomic_pipeline_without_real_generation(self):
        source = io.BytesIO()
        Image.new('RGB', (4, 4), 'black').save(source, format='PNG')
        data = GenerateRequest(**self.payload)
        destination = self.output/'dark/S207352.jpg'
        with patch('api.download_image', return_value=source.getvalue()), patch('api.cutout', return_value=Image.new('RGBA', (4, 4))), patch('api.compose', return_value=Image.new('RGB', (4, 4), 'red')):
            render_card(data, destination)
        with Image.open(destination) as result:
            self.assertEqual(result.format, 'JPEG')
            self.assertEqual(result.size, (4, 4))
            self.assertTrue(all(abs(a-b) <= 2 for a,b in zip(result.getpixel((0, 0)), (255, 0, 0))))
        self.assertEqual(list(destination.parent.glob('.card-*')), [])

    def test_private_image_hosts_are_rejected(self):
        from fastapi import HTTPException
        for url in ('http://127.0.0.1/a.png', 'http://[::1]/a.png', 'http://10.0.0.1/a.png'):
            with self.assertRaises(HTTPException) as error:
                download_image(url)
            self.assertEqual(error.exception.status_code, 422)


if __name__ == '__main__':
    unittest.main()
