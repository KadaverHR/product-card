"""Feed skip/continuation tests without downloads or image generation."""
import copy
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from http.client import IncompleteRead
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

from feed_card import IncompleteOffer, generate, main, validate_product, read_offers, prepared_feed, offer_data


class FeedResponse(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {} if length is None else {'Content-Length': str(length)}


class FeedTest(unittest.TestCase):
    def setUp(self):
        self.product = {
            'id': 'GOOD', 'pictures': ['https://example.com/photo.jpg'],
            'card': dict(brand='Cordiant', model='Model', size='225/65',
                         diameter='R17', load='102/104', speed='H'),
        }

    def test_missing_required_data_is_rejected_before_any_download(self):
        for key in self.product['card']:
            for value in (None, '', '   '):
                with self.subTest(key=key, value=value):
                    product = copy.deepcopy(self.product)
                    product['card'][key] = value
                    with patch('feed_card.open_url') as download:
                        with self.assertRaises(IncompleteOffer) as error:
                            generate(product, SimpleNamespace())
                        self.assertIn(key, str(error.exception))
                        download.assert_not_called()
        for key in ('id', 'pictures', 'card'):
            product = copy.deepcopy(self.product)
            del product[key]
            with self.assertRaises(IncompleteOffer):
                validate_product(product)
        validate_product(self.product)  # Season is optional; double index is valid.

    def test_s083924_size_without_profile_and_model_extraction(self):
        element = ET.fromstring('''<offer id="S083924" available="true">
          <vendor>Cordiant</vendor><name>Cordiant Business CA-1 195R14 106/104R</name>
          <picture>https://example.com/photo.jpg</picture>
          <param name="Размер">195R14 106/104R</param>
          <param name="Ширина">195</param><param name="Диаметр">14</param>
          <param name="Индекс нагрузки">106/104</param>
          <param name="Индекс скорости">R</param></offer>''')
        product = offer_data(element, 'https://example.com/feed.xml')
        self.assertEqual(product['card'], dict(brand='Cordiant', model='Business CA-1',
                         size='195', diameter='R14', load='106/104', speed='R', season_label='ШИНЫ'))
        validate_product(product)

    def test_sizes_with_and_without_profile_parse_from_name(self):
        for supplied, size, diameter, load, speed in [
                ('195R14 106/104R', '195', 'R14', '106/104', 'R'),
                ('195 R14 106R', '195', 'R14', '106', 'R'),
                ('195R14C 106/104R', '195', 'R14', '106/104', 'R'),
                ('225/65 R17 106H', '225/65', 'R17', '106', 'H'),
                ('225/65R17 106/104H', '225/65', 'R17', '106/104', 'H')]:
            with self.subTest(size=supplied):
                product = offer_data(ET.fromstring(f'''<offer id="GOOD"><vendor>Cordiant</vendor>
                    <name>Cordiant Business CA-1 {supplied}</name>
                    <picture>https://example.com/photo.jpg</picture></offer>'''), 'https://example.com/feed.xml')
                self.assertEqual(product['card']['size'], size)
                self.assertEqual(product['card']['diameter'], diameter)
                self.assertEqual(product['card']['load'], load)
                self.assertEqual(product['card']['speed'], speed)
                self.assertEqual(product['card']['model'], 'Business CA-1')
                validate_product(product)

    def test_width_alone_does_not_become_size_without_profile(self):
        product = offer_data(ET.fromstring('''<offer id="GOOD"><vendor>Cordiant</vendor>
            <model>Business CA-1</model><param name="Ширина">195</param>
            <param name="Диаметр">14</param></offer>'''), 'https://example.com/feed.xml')
        self.assertEqual(product['card']['size'], '')
        with self.assertRaises(IncompleteOffer):
            validate_product(product)

    def run_feed(self, xml, renderer):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'feed.xml'
            source.write_text(xml, encoding='utf-8')
            output = io.StringIO()
            errors = io.StringIO()
            args = ['feed_card.py', '--feed', str(source), '--limit', '0',
                    '--output', str(Path(directory)/'cards')]
            with patch('sys.argv', args), patch('feed_card.generate', side_effect=renderer), \
                    redirect_stdout(output), redirect_stderr(errors):
                result = main()
            return result, output.getvalue(), errors.getvalue()

    def test_incomplete_offers_do_not_stop_real_xml_iteration(self):
        xml = '''<yml_catalog><shop><offers>
          <offer id="BAD"><vendor>Cordiant</vendor><model>Model</model></offer>
          <offer id="GOOD"><vendor>Cordiant</vendor><model>Model</model>
            <param name="Размер">225/65 R17 106H</param>
            <picture>https://example.com/photo.jpg</picture></offer>
          <offer><name>No ID or data</name></offer>
        </offers></shop></yml_catalog>'''
        generated = []
        def render(product, args):
            validate_product(product)
            generated.append(product['id'])
        result, output, errors = self.run_feed(xml, render)
        self.assertEqual(result, 0)
        self.assertEqual(generated, ['GOOD'])
        self.assertIn('SKIP BAD:', output)
        self.assertIn('SKIP <missing-id>:', output)
        self.assertIn('processed=3, generated=1, skipped=2, failed=0', output)
        self.assertEqual(errors, '')

    def test_real_generation_errors_continue_but_return_failure(self):
        seen = []
        def render(product, args):
            seen.append(product['id'])
            if product['id'] == 'BAD':
                raise OSError('image download failed')
        result, output, errors = self.run_feed(
            '<offers><offer id="BAD"/><offer id="GOOD"/></offers>', render)
        self.assertEqual(seen, ['BAD', 'GOOD'])
        self.assertEqual(result, 1)
        self.assertIn('ERROR BAD:', errors)
        self.assertIn('processed=2, generated=1, skipped=0, failed=1', output)

    def test_all_incomplete_offers_are_successfully_skipped(self):
        result, output, _ = self.run_feed('<offers><offer id="BAD"/></offers>',
                                         lambda product, args: validate_product(product))
        self.assertEqual(result, 0)
        self.assertIn('processed=1, generated=0, skipped=1, failed=0', output)

    def test_malformed_feed_still_returns_failure(self):
        result, _, errors = self.run_feed('<offers><offer>', lambda product, args: None)
        self.assertEqual(result, 1)
        self.assertIn('Feed error:', errors)

    def test_download_retries_incomplete_read_and_preserves_relative_urls(self):
        broken = FeedResponse(b'')
        broken.read = lambda size: (_ for _ in ()).throw(IncompleteRead(b'partial', 10))
        xml = b'<offers><offer id="GOOD"><picture>photos/tyre.jpg</picture></offer></offers>'
        with patch('feed_card.open_url', side_effect=[broken, FeedResponse(xml, len(xml))]) as download, \
                patch('feed_card.time.sleep') as sleep, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            products = list(read_offers('https://example.com/catalog/feed.xml', limit=0))
        self.assertEqual(download.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertEqual(products[0]['pictures'], ['https://example.com/catalog/photos/tyre.jpg'])

    def test_length_mismatch_and_truncated_xml_are_retried(self):
        xml = b'<offers><offer id="GOOD"/></offers>'
        responses = [FeedResponse(xml, len(xml)+10),
                     FeedResponse(b'<offers><offer id="BAD"/>'), FeedResponse(xml)]
        with patch('feed_card.open_url', side_effect=responses) as download, \
                patch('feed_card.time.sleep'), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            products = list(read_offers('https://example.com/feed.xml', limit=0))
        self.assertEqual(download.call_count, 3)
        self.assertEqual([p['id'] for p in products], ['GOOD'])

    def test_incomplete_feed_never_yields_offers_before_validation(self):
        with patch('feed_card.open_url', side_effect=lambda *args: FeedResponse(b'<offers><offer id="BAD"/>')) as download, \
                patch('feed_card.time.sleep'), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            iterator = read_offers('https://example.com/feed.xml', attempts=2)
            with self.assertRaises(ET.ParseError):
                next(iterator)
        self.assertEqual(download.call_count, 2)

    def test_snapshot_is_removed_after_reading(self):
        with patch('feed_card.open_url', return_value=FeedResponse(b'<offers/>')), redirect_stdout(io.StringIO()):
            with prepared_feed('https://example.com/feed.xml') as path:
                self.assertTrue(path.is_file())
            self.assertFalse(path.exists())

    def test_invalid_local_xml_does_not_generate_even_with_limit_one(self):
        with patch('feed_card.generate') as render:
            result, _, _ = self.run_feed('<offers><offer id="GOOD"/><offer>', render)
        render.assert_not_called()
        self.assertEqual(result, 1)


if __name__ == '__main__':
    unittest.main()
