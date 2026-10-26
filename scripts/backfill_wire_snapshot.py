"""
Backfill wire_scan_detail text snapshots + part_no/jx_eo/vehicle_type stamps
for historical wire_scan_record rows.

Idempotent:
- (lot_no, circuit_no) pairs that already have snapshot rows are skipped.
- Records already stamped keep their values.

Usage (project root):
  python scripts/backfill_wire_snapshot.py
"""

import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app_factory import create_app, db
from models.wire_scan_record import WireScanRecord
from models.lot_login_info import LotLoginInfo
from models.wire_scan_detail import WireScanDetail
from routes import _wire_ensure_snapshot, _wire_stamp_lot_records


def main():
    app = create_app()
    with app.app_context():
        # 1) 批量预取 LOT 登录信息
        all_lots = [r[0] for r in db.session.query(WireScanRecord.lot_no).distinct().all()]
        lot_map = {}
        if all_lots:
            for lr in LotLoginInfo.query.filter(LotLoginInfo.lot_no.in_(all_lots)).all():
                lot_map[lr.lot_no] = {'part_no': lr.part_no, 'jx_eo': lr.jx_eo,
                                      'vehicle_type': lr.vehicle_type}

        # 2) 无回路号的记录：仅补盖品番/EO/车种别
        no_circuit_lots = [r[0] for r in db.session.query(WireScanRecord.lot_no)
                           .filter(WireScanRecord.circuit_no.is_(None)).distinct().all()]
        stamped = 0
        for lot in no_circuit_lots:
            li = lot_map.get(lot)
            if li and li.get('part_no'):
                _wire_stamp_lot_records(lot, li.get('part_no'), li.get('jx_eo'),
                                        li.get('vehicle_type') or '')
                stamped += 1
        db.session.commit()

        # 3) 每个 (LOT, 回路号) 补固化文本快照（已存在快照的自动跳过）
        pairs = (db.session.query(WireScanRecord.lot_no, WireScanRecord.circuit_no)
                 .filter(WireScanRecord.circuit_no.isnot(None))
                 .distinct().all())
        total = len(pairs)
        with_snap = 0
        no_material = 0
        for idx, (lot, circ) in enumerate(pairs, 1):
            details = _wire_ensure_snapshot(lot, circ, lot_info_map=lot_map, commit=True)
            if details:
                with_snap += 1
            else:
                no_material += 1
            if idx % 50 == 0 or idx == total:
                print(f'  进度 {idx}/{total}')

        total_detail = db.session.query(WireScanDetail.id).count()
        print('=' * 60)
        print(f'扫描组合总数: {total}')
        print(f'已固化文本快照组合: {with_snap}（快照行 {total_detail} 条）')
        print(f'无资料/未登录组合: {no_material}')
        print(f'无回路号补盖章 LOT 数: {stamped}')
        print('回填完成')


if __name__ == '__main__':
    main()
