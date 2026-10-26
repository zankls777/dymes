"""迁移脚本：把 static/uploads 根目录下的平铺文件移入按类型划分的子文件夹。

迁移规则：
  - 文件名含 SubList   -> static/uploads/SubList/
  - 文件名含 UsageList -> static/uploads/UsageList/
  - 其他无法识别的文件保持原位不动

同时回写数据库中 file_info.category 字段，便于后续按子目录定位物理文件。
脚本幂等：可重复执行，已在子目录中的文件会被跳过。

Usage:
  python scripts/migrate_uploads_to_subfolders.py            # 执行迁移
  python scripts/migrate_uploads_to_subfolders.py --dry-run  # 仅预览，不落盘
"""

import argparse
import os
import shutil
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app_factory import create_app, db
from models.file_info import FileInfo


def detect_file_type(filename):
    name = filename.lower()
    if 'sublist' in name:
        return 'SubList'
    if 'usagelist' in name:
        return 'UsageList'
    return None


def ensure_category_column():
    """确保 file_info.category 列存在（脚本独立运行时兜底建列）"""
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    cols = {c['name'] for c in inspector.get_columns('file_info')}
    if 'category' not in cols:
        db.session.execute(text("ALTER TABLE file_info ADD COLUMN category VARCHAR(50)"))
        db.session.commit()


def main():
    parser = argparse.ArgumentParser(description='Move flat upload files into type subfolders')
    parser.add_argument('--dry-run', action='store_true', help='仅预览，不实际移动文件')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        root = os.path.join(app.root_path, 'static', 'uploads')
        if not os.path.isdir(root):
            print(f'上传根目录不存在: {root}')
            return

        # 保证 category 列存在，否则 ORM 查询会中断整个迁移
        if not args.dry_run:
            ensure_category_column()

        moved = skipped = 0
        for name in sorted(os.listdir(root)):
            src = os.path.join(root, name)
            if not os.path.isfile(src):
                continue  # 跳过子目录（已迁移文件所在目录）

            category = detect_file_type(name)
            if not category:
                skipped += 1
                continue

            dest_dir = os.path.join(root, category)
            dest = os.path.join(dest_dir, name)

            if os.path.exists(dest):
                # 目标已存在同名文件，避免覆盖，跳过
                skipped += 1
                continue

            print(f'{name} -> {category}/')
            if not args.dry_run:
                os.makedirs(dest_dir, exist_ok=True)
                shutil.move(src, dest)
                # 回写 category 字段；DB 异常不影响文件已移动的结果
                try:
                    rec = FileInfo.query.filter_by(filename=name).first()
                    if rec and rec.category != category:
                        rec.category = category
                        db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    print(f'  警告：写入 category 失败（文件已移动）：{e}')
            moved += 1

        action = '预览' if args.dry_run else '迁移'
        print(f'\n{action}完成：移动 {moved} 个，跳过 {skipped} 个。')


if __name__ == '__main__':
    main()
