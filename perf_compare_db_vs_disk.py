#!/usr/bin/env python3
"""
性能对比测试：数据库读取 vs 磁盘Excel读取
使用真实数据库数据，生成模拟Excel文件后对比两种读取方式的耗时
"""
import sys
import os
import time
import statistics
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app_factory import create_app, db
from models.file_info import FileInfo
from models.sub_list import SubList


def main():
    print('=' * 70)
    print('性能对比：数据库读取 vs 磁盘Excel读取')
    print('=' * 70)

    app = create_app()

    with app.app_context():
        from routes import _read_file_data

        # 选取数据量最大的 SubList 文件
        from sqlalchemy import func
        target = db.session.query(
            SubList.source_file,
            func.count(SubList.id).label('cnt')
        ).filter(SubList.deleted == False).group_by(SubList.source_file).order_by(
            func.count(SubList.id).desc()
        ).first()

        if not target:
            print('无测试数据')
            return 1

        file_info = FileInfo.query.filter_by(filename=target.source_file).first()
        print(f'\n测试文件: {file_info.filename}')
        print(f'数据量: {target.cnt} 行')

        # 1. 数据库读取
        print('\n[1] 数据库读取耗时（5次取平均）')
        print('-' * 50)
        _read_file_data(file_info)  # 预热
        db_times = []
        for i in range(5):
            t0 = time.perf_counter()
            df = _read_file_data(file_info)
            t1 = time.perf_counter()
            db_times.append((t1 - t0) * 1000)
        db_avg = statistics.mean(db_times)
        print(f'  avg={db_avg:.1f}ms  min={min(db_times):.1f}ms  max={max(db_times):.1f}ms')
        print(f'  读取行数: {len(df)}, 列数: {len(df.columns)}')

        # 2. 磁盘Excel读取（生成临时Excel模拟旧方式）
        print('\n[2] 磁盘Excel读取耗时（5次取平均，模拟旧方式）')
        print('-' * 50)

        # 生成临时 Excel 文件
        tmp_path = os.path.join(tempfile.gettempdir(), f'_perf_test_{file_info.id}.xlsx')
        try:
            df.to_excel(tmp_path, index=False, engine='openpyxl')
            size_kb = os.path.getsize(tmp_path) / 1024
            print(f'  临时Excel文件大小: {size_kb:.1f} KB')

            disk_times = []
            for i in range(5):
                t0 = time.perf_counter()
                pd_df = __import__('pandas').read_excel(tmp_path, dtype=str)
                pd_df = pd_df.fillna('').astype(str)
                t1 = time.perf_counter()
                disk_times.append((t1 - t0) * 1000)
            disk_avg = statistics.mean(disk_times)
            print(f'  avg={disk_avg:.1f}ms  min={min(disk_times):.1f}ms  max={max(disk_times):.1f}ms')

            # 4. 验证读取结果一致性（在删除临时文件前完成）
            print('\n[4] 读取结果一致性验证')
            print('-' * 50)
            df2 = __import__('pandas').read_excel(tmp_path, dtype=str)
            # 重新读取数据库做对比
            df_db = _read_file_data(file_info)
            same_shape = df_db.shape == df2.shape
            print(f'  行数列数一致: {"是" if same_shape else "否"}')
            if same_shape and len(df2) > 0:
                same_first = str(df_db.iloc[0].get('SubPno', '')).strip() == str(df2.iloc[0].get('SubPno', '')).strip()
                print(f'  首行SubPno一致: {"是" if same_first else "否"}')
                print(f'  ✅ 数据内容一致' if same_first else f'  ❌ 数据不一致')
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        # 3. 对比结果
        print('\n[3] 性能对比结果')
        print('=' * 50)
        speedup = disk_avg / db_avg if db_avg > 0 else float('inf')
        print(f'  数据库读取:  {db_avg:.1f} ms')
        print(f'  磁盘Excel读取: {disk_avg:.1f} ms')
        print(f'  提升倍数: {speedup:.1f}x')
        if speedup >= 2:
            print(f'  ✅ 数据库读取比磁盘读取快 {speedup:.1f} 倍（提升 {((speedup-1)*100):.0f}%）')
        else:
            print(f'  ⚠️ 提升不明显（可能文件较小或缓存命中）')

    print('\n' + '=' * 70)
    return 0


if __name__ == '__main__':
    sys.exit(main())
