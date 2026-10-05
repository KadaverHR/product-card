"""Feed skip/continuation tests without downloads or image generation."""
import copy
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from feed_card import IncompleteOffer, generate, main, validate_product


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


if __name__ == '__main__':
    unittest.main()
