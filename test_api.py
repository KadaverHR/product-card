"""API contract tests without external downloads or real card generation."""
import io
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
        self.client = TestClient(create_app(self.output, 'https://cards.example.com', 'secret'))
        self.headers = {'X-API-Key': 'secret'}
        self.payload = dict(theme='dark', sku='S207352', brand='Cordiant', model='GRAVITY SUV',
                            size='225/65 R17', load_index='106', speed_index='H',
                            image_url='https://example.com/photo.jpg', season='summer')

    def test_theme_is_required_and_key_is_checked(self):
        self.assertEqual(self.client.get('/api/v1/cards?theme=dark').status_code, 401)
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

    def test_generate_returns_link_and_publishes_to_correct_theme(self):
        def fake_render(data, path):
            self.assertEqual(data.card_data()['diameter'], 'R17')
            self.assertEqual(data.card_data()['season_label'], '\u041b\u0415\u0422\u041d\u0418\u0415 \u0428\u0418\u041d\u042b')
            path.write_bytes(b'new-card')
        with patch('api.render_card', side_effect=fake_render):
            response = self.client.post('/api/v1/cards/generate', json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sku'], 'S207352')
        self.assertTrue((self.output/'dark/S207352.png').is_file())
        self.assertFalse((self.output/'light/S207352.png').exists())

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
        destination = self.output/'dark/S207352.png'
        with patch('api.download_image', return_value=source.getvalue()), patch('api.cutout', return_value=Image.new('RGBA', (4, 4))), patch('api.compose', return_value=Image.new('RGB', (4, 4), 'red')):
            render_card(data, destination)
        with Image.open(destination) as result:
            self.assertEqual(result.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(list(destination.parent.glob('.card-*')), [])

    def test_private_image_hosts_are_rejected(self):
        from fastapi import HTTPException
        for url in ('http://127.0.0.1/a.png', 'http://[::1]/a.png', 'http://10.0.0.1/a.png'):
            with self.assertRaises(HTTPException) as error:
                download_image(url)
            self.assertEqual(error.exception.status_code, 422)


if __name__ == '__main__':
    unittest.main()
