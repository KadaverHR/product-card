"""Create JPEG copies of existing PNG cards without deleting the originals."""
import argparse
from pathlib import Path

from PIL import Image

from card_output import save_card


def convert_cards(output, theme='all'):
    converted = skipped = failed = 0
    for selected in (('dark', 'light') if theme == 'all' else (theme,)):
        for source in sorted((Path(output)/selected).glob('*.png')):
            if not source.is_file():
                continue
            destination = source.with_suffix('.jpg')
            if destination.exists():
                skipped += 1
                continue
            try:
                with Image.open(source) as image:
                    save_card(image, destination)
                converted += 1
                print(f'{source.name}: {source.stat().st_size} -> {destination.stat().st_size} bytes', flush=True)
            except Exception as error:
                failed += 1
                print(f'ERROR {source.name}: {error}', flush=True)
    print(f'Summary: converted={converted}, skipped={skipped}, failed={failed}', flush=True)
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Existing card directory with dark/light subfolders')
    parser.add_argument('--theme', choices=('dark', 'light', 'all'), default='all')
    args = parser.parse_args()
    if not args.output.is_dir():
        parser.error('output must be an existing directory')
    return convert_cards(args.output, args.theme)


if __name__ == '__main__':
    raise SystemExit(main())
