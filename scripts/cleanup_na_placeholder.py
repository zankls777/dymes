"""一次性清理：删除手动新规旧逻辑产生的 NA 占位文件记录（file_info）。

仅删除同时满足以下条件的记录：
  1) filename 以 'NA_' 开头，且以 '_SubList.xlsx' 或 '_UsageList.xlsx' 结尾
  2) filesize = 0（占位空文件）

用法（在生产环境项目根目录）：
    python scripts/cleanup_na_placeholder.py          # 仅预览
    python scripts/cleanup_na_placeholder.py --apply   # 实际删除
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_factory import create_app  # noqa: E402
from db import db  # noqa: E402
from models.file_info import FileInfo  # noqa: E402

app = create_app()
apply = '--apply' in sys.argv

with app.app_context():
    rows = FileInfo.query.filter(FileInfo.filename.like(r'NA\_%')).all()
    # 仅保留占位空文件（0 字节）且为标准两份文件的记录
    rows = [r for r in rows
            if (r.filesize or 0) == 0
            and r.filename.endswith(('_SubList.xlsx', '_UsageList.xlsx'))]

    print(f'待清理 FileInfo 记录数：{len(rows)}')
    for r in rows:
        print(f'  id={r.id} filename={r.filename} filesize={r.filesize} time={r.upload_time}')

    if not rows:
        print('无需清理。')
    elif not apply:
        print('\n[预览模式] 未做任何修改。确认无误后加 --apply 执行删除。')
    else:
        for r in rows:
            db.session.delete(r)
        db.session.commit()
        print(f'\n已删除 {len(rows)} 条记录。')

        try:
            from query_cache import query_cache_manager
            query_cache_manager.invalidate_list_cache()
            print('已清理查询缓存。')
        except Exception as e:  # noqa: BLE001
            print(f'缓存清理跳过：{e}')
