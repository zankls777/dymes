"""
Backfill business tables (sub_list / usage_list) from existing Excel files
located in the uploads directory. This reuses the on-disk readers to parse
Excel and insert rows, skipping records that already exist for the same
source_file.

Usage:
  source SUB/bin/activate  # or your env
  python scripts/backfill_from_uploads.py --dir /var/www/dayan/dayan/static/uploads --limit-days 0

Notes:
  - Only processes files whose filename contains 'SubList' or 'UsageList'.
  - Idempotent: if records for a given source_file already exist, that file is skipped.
"""

import argparse
import os
import sys
from pathlib import Path

# Ensure project root on sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app_factory import create_app, db
from models.sub_list import SubList
from models.usage_list import UsageList
from flask import current_app

# 本地实现：从磁盘路径读取 Excel（仅本维护脚本使用，不依赖 routes 的已删除函数）
def read_excel_from_path(path: Path):
    """从磁盘路径读取 Excel 文件为 DataFrame（兼容 xls/xlsx）。"""
    import pandas as pd
    ext = path.suffix.lower()
    try:
        if ext == '.xlsx':
            df = pd.read_excel(str(path), engine='openpyxl')
        else:
            import warnings
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message='.*OLE2 inconsistency.*')
                df = pd.read_excel(str(path), engine='xlrd')
    except Exception:
        df = pd.read_excel(str(path))
    df = df.fillna('').astype(str)
    return df


def extract_vehicle_type_from_filename(filename: str):
    """从文件名中提取车种部分（与 routes 中的解析规则一致）。"""
    import os
    try:
        base = os.path.basename(filename or '')
        name_wo_ext = os.path.splitext(base)[0]
        parts = name_wo_ext.split('_')
        if len(parts) >= 4:
            return '_'.join(parts[:-3])
    except Exception:
        pass
    return None


def process_sublist(path: Path) -> int:
    """Parse a SubList excel file and insert records. Returns inserted count."""
    source = path.name
    # Skip if already imported
    if SubList.query.filter_by(source_file=source).first():
        return 0
    df = read_excel_from_path(path)
    if df is None or df.empty:
        return 0
    # Normalize columns (best-effort; missing columns will default to empty string)
    def get(col):
        return df.get(col) if col in df.columns else [''] * len(df)

    inserted = 0
    for i in range(len(df)):
        rec = SubList(
            SubPno=str(df.iloc[i].get('SubPno', '')),
            EoKsc=str(df.iloc[i].get('EoKsc', '')),
            GroupNo=str(df.iloc[i].get('GroupNo', '')),
            GroupOrder=str(df.iloc[i].get('GroupOrder', '')),
            SetCode=str(df.iloc[i].get('SetCode', '')),
            CutCode=str(df.iloc[i].get('CutCode', '')),
            Circuit=str(df.iloc[i].get('Circuit', '')),
            Wire=str(df.iloc[i].get('Wire', '')),
            WireCode=str(df.iloc[i].get('WireCode', '')),
            Color=str(df.iloc[i].get('Color', '')),
            SQ=str(df.iloc[i].get('SQ', '')),
            Length_Cut=str(df.iloc[i].get('Length_Cut', '')),
            Length_Cut2=str(df.iloc[i].get('Length_Cut2', '')),
            Tml1=str(df.iloc[i].get('Tml1', '')),
            Tml2=str(df.iloc[i].get('Tml2', '')),
            Strip1=str(df.iloc[i].get('Strip1', '')),
            Strip2=str(df.iloc[i].get('Strip2', '')),
            Rel1=str(df.iloc[i].get('Rel1', '')),
            Rel2=str(df.iloc[i].get('Rel2', '')),
            Seal1=str(df.iloc[i].get('Seal1', '')),
            Seal2=str(df.iloc[i].get('Seal2', '')),
            SubComp1=str(df.iloc[i].get('SubComp1', '')),
            SubComp2=str(df.iloc[i].get('SubComp2', '')),
            M_ST1=str(df.iloc[i].get('M_ST1', '')),
            M_ST2=str(df.iloc[i].get('M_ST2', '')),
            M_ST3=str(df.iloc[i].get('M_ST3', '')),
            M_ST4=str(df.iloc[i].get('M_ST4', '')),
            Joint_Points=str(df.iloc[i].get('Joint_Points', '')),
            Remark1=str(df.iloc[i].get('Remark1', '')),
            Remark2=str(df.iloc[i].get('Remark2', '')),
            source_file=source,
        )
        db.session.add(rec)
        inserted += 1
    db.session.commit()
    return inserted


def process_usagelist(path: Path) -> int:
    source = path.name
    if UsageList.query.filter_by(source_file=source).first():
        return 0
    df = read_excel_from_path(path)
    if df is None or df.empty:
        return 0
    vehicle_type = extract_vehicle_type_from_filename(source) or ''
    inserted = 0
    for i in range(len(df)):
        rec = UsageList(
            SubPno=str(df.iloc[i].get('SubPno', '')),
            EoKsc=str(df.iloc[i].get('EoKsc', '')),
            VehicleType=vehicle_type,
            Comp_Kind=str(df.iloc[i].get('Comp_Kind', '')),
            Detail_Kind=str(df.iloc[i].get('Detail_Kind', '')),
            KscPno=str(df.iloc[i].get('KscPno', '')),
            Part_Name=str(df.iloc[i].get('Part_Name', '')),
            Usage=str(df.iloc[i].get('Usage', '')),
            Length=str(df.iloc[i].get('Length', '')),
            source_file=source,
        )
        db.session.add(rec)
        inserted += 1
    db.session.commit()
    return inserted


def main():
    parser = argparse.ArgumentParser(description='Backfill SubList/UsageList from existing uploads')
    parser.add_argument('--dir', dest='directory', default=None, help='Uploads directory')
    parser.add_argument('--limit-days', dest='limit_days', type=int, default=0, help='Only process files modified within N days (0=all)')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        src_dir = args.directory or current_app.config.get('UPLOAD_FOLDER')
        if not src_dir:
            src_dir = os.path.join(app.root_path, 'static', 'uploads')
        p = Path(src_dir)
        if not p.exists():
            print(f'Directory not found: {p}')
            return

        from datetime import datetime, timedelta
        cutoff = None
        if args.limit_days and args.limit_days > 0:
            cutoff = datetime.now() - timedelta(days=args.limit_days)

        files = [f for f in sorted(p.glob('*')) if f.is_file()]
        total_inserted = 0
        for f in files:
            name = f.name.lower()
            if 'sublist' not in name and 'usagelist' not in name:
                continue
            if cutoff and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                continue
            try:
                if 'sublist' in name:
                    cnt = process_sublist(f)
                    print(f'SubList {f.name}: +{cnt}')
                else:
                    cnt = process_usagelist(f)
                    print(f'UsageList {f.name}: +{cnt}')
                total_inserted += cnt
            except Exception as e:
                print(f'Error processing {f.name}: {e}')
                db.session.rollback()

        print(f'Done. Inserted {total_inserted} records in total.')


if __name__ == '__main__':
    main()


