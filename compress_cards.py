"""Resume-safe, one-file-at-a-time compression of existing JPG and PNG cards."""
import argparse
import json
import sqlite3
from pathlib import Path

from PIL import Image

from card_output import compression_profile, save_card


def compress_cards(output, *, theme='all', quality=None, max_edge=None, subsampling=None,
                   dry_run=False, convert_png=False, remove_png=False, limit=0):
    output = Path(output).resolve()
    if not output.is_dir():
        raise ValueError('output must be an existing directory')
    if remove_png and not convert_png:
        raise ValueError('--remove-png requires --convert-png')
    profile = compression_profile(quality, max_edge, subsampling)
    profile_id = json.dumps(profile)
    db = None
    if not dry_run:
        # This is a new maintenance ledger, not any of the product databases.
        db = sqlite3.connect(output/'.card-compression.sqlite3')
        db.execute('CREATE TABLE IF NOT EXISTS done (path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, profile TEXT)')
        db.commit()
    processed = changed = skipped = failed = saved = 0
    try:
        for selected in (('dark', 'light') if theme == 'all' else (theme,)):
            folder = output/selected
            for source in sorted(folder.iterdir()) if folder.is_dir() else ():
                if limit and processed >= limit:
                    break
                if source.suffix.lower() not in ('.jpg', '.png') or not source.is_file() or source.is_symlink():
                    continue
                is_png = source.suffix.lower() == '.png'
                if is_png and not convert_png:
                    continue
                processed += 1
                destination = source.with_suffix('.jpg') if is_png else source
                key = str(source.relative_to(output))
                before = source.stat()
                if is_png and destination.exists():
                    # Never delete a PNG just because an unrelated JPEG shares its name.
                    skipped += 1
                    print(f'SKIP {key}: JPG already exists; PNG preserved', flush=True)
                    continue
                if db:
                    row = db.execute('SELECT size, mtime, profile FROM done WHERE path=?', (key,)).fetchone()
                    if row == (before.st_size, before.st_mtime_ns, profile_id):
                        skipped += 1
                        continue
                try:
                    with Image.open(source) as image:
                        image.load()
                        if dry_run:
                            # Estimate without creating a second on-disk collection.
                            import io
                            temporary = image.convert('RGB')
                            temporary.thumbnail((profile[1], profile[1]), Image.Resampling.LANCZOS)
                            data = io.BytesIO()
                            temporary.save(data, 'JPEG', quality=profile[0], subsampling=profile[2], optimize=True, progressive=True)
                            after_size = len(data.getbuffer())
                            replaced = is_png or after_size < before.st_size
                        else:
                            replaced = save_card(image, destination, quality=profile[0], max_edge=profile[1],
                                                 subsampling=profile[2], only_smaller=not is_png)
                            after_size = destination.stat().st_size
                    if replaced:
                        changed += 1
                        reclaimed = max(0, before.st_size - after_size)
                        # PNG copies reclaim disk space only when removal was requested.
                        saved += reclaimed if not is_png or remove_png else 0
                        if is_png and remove_png and not dry_run:
                            source.unlink()  # Only after full decoding/verification of our new JPG.
                        print(f'{"PLAN" if dry_run else "OK"} {key}: {before.st_size} -> {after_size} bytes', flush=True)
                    else:
                        skipped += 1
                    if db and source.exists():
                        current = source.stat()
                        db.execute('INSERT OR REPLACE INTO done VALUES (?, ?, ?, ?)',
                                   (key, current.st_size, current.st_mtime_ns, profile_id))
                    if db and is_png and replaced:
                        current = destination.stat()
                        db.execute('INSERT OR REPLACE INTO done VALUES (?, ?, ?, ?)',
                                   (str(destination.relative_to(output)), current.st_size, current.st_mtime_ns, profile_id))
                    if db:
                        db.commit()
                except Exception as error:
                    failed += 1
                    print(f'ERROR {key}: {error}', flush=True)
        print(f'Summary: processed={processed}, changed={changed}, skipped={skipped}, failed={failed}, saved_bytes={saved}', flush=True)
    finally:
        if db:
            db.close()
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--theme', choices=('dark', 'light', 'all'), default='all')
    parser.add_argument('--quality', type=int, default=82)
    parser.add_argument('--max-edge', type=int, default=1200)
    parser.add_argument('--subsampling', type=int, choices=(0, 1, 2), default=2, help='0: 4:4:4, 2: 4:2:0')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--convert-png', action='store_true')
    parser.add_argument('--remove-png', action='store_true', help='Delete ONLY PNGs newly converted and verified by this run; breaks their PNG links')
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('limit must be >= 0')
    try:
        return compress_cards(**vars(args))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'Compression failed: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
