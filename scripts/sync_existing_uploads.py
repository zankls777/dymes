"""
Scan a directory of existing uploaded files and register them into the DB
so they appear in the File Management page. No actual upload is performed;
this only creates missing FileInfo records for files on disk.

Usage:
  # Activate venv and run from project root
  # Optional: specify directory; default uses app.config['UPLOAD_FOLDER'] or app_root/static/uploads
  python scripts/sync_existing_uploads.py --dir /var/www/dayan/dayan/uploads

Notes:
  - File type is inferred by filename containing 'SubList' or 'UsageList'.
  - Upload time is taken from file's mtime.
  - Skips files already present (by filename match).
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on sys.path when running as a script from scripts/
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app_factory import create_app
from models.file_info import FileInfo
from app_factory import db


def infer_file_type(filename: str) -> str:
    name = filename.lower()
    if 'sublist' in name:
        return 'SubList'
    if 'usagelist' in name:
        return 'UsageList'
    # Fallback: use extension-based default
    return 'SubList'


def main():
    parser = argparse.ArgumentParser(description='Register existing upload files into DB')
    parser.add_argument('--dir', dest='directory', default=None, help='Directory containing existing uploads')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        # Determine source directory
        src_dir = args.directory or app.config.get('UPLOAD_FOLDER')
        if not src_dir:
            src_dir = os.path.join(app.root_path, 'static', 'uploads')

        src_path = Path(src_dir)
        if not src_path.exists():
            print(f"Directory not found: {src_path}")
            return

        print(f"Scanning directory: {src_path}")

        existing = {f.filename for f in FileInfo.query.with_entities(FileInfo.filename).all()}
        to_add = []

        for p in sorted(src_path.glob('*')):
            if not p.is_file():
                continue
            if p.name in existing:
                continue

            stat = p.stat()
            upload_time = datetime.fromtimestamp(stat.st_mtime)
            file_type = infer_file_type(p.name)
            filesize = stat.st_size

            rec = FileInfo(
                filename=p.name,
                file_type=file_type,
                upload_time=upload_time,
                filesize=filesize,
            )
            to_add.append(rec)

        if not to_add:
            print('No new files to add. DB is in sync with directory.')
            return

        for rec in to_add:
            db.session.add(rec)
        db.session.commit()
        print(f"Inserted {len(to_add)} file records into DB.")


if __name__ == '__main__':
    main()


