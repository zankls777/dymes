# -*- coding: utf-8 -*-
"""Backfill historical investment_record rows whose circuits were wrongly stored as
the sheet's TOTAL column (qty x per-unit circuits).

Bug signature (per source_file): rows with qty>=2 ALL satisfy circuits % qty == 0
with quotient in [1, 2000] (per-unit circuit count). Correct files fail this by
construction (per-unit circuits are independent of qty).

Backfill: new_circuits = circuits / qty ; new_total_circuits = circuits (old value).

Usage:
  python backfill_invest_circuits.py            # dry run, report only
  python backfill_invest_circuits.py --apply    # write changes
"""
import sys
import io
sys.path.insert(0, r'f:\TA\bom3')

APPLY = '--apply' in sys.argv

log_buf = io.StringIO()
def log(*a):
    line = ' '.join(str(x) for x in a)
    print(line)
    log_buf.write(line + '\n')

from app_factory import create_app
from db import db
from models.invest_record import InvestmentRecord
from sqlalchemy import func

app = create_app()
with app.app_context():
    files = [r[0] for r in db.session.query(InvestmentRecord.source_file)
             .filter(InvestmentRecord.source_file.isnot(None))
             .distinct().all()]

    log(f'source files: {len(files)}, mode: {"APPLY" if APPLY else "DRY-RUN"}')
    log('=' * 70)

    total_flagged_files = 0
    total_update_rows = 0
    skipped_ambiguous = []

    for src in sorted(files):
        rows = InvestmentRecord.query.filter_by(source_file=src).all()
        qty_rows = [r for r in rows if (r.qty or 0) > 0]
        strong = [r for r in qty_rows if r.qty >= 2 and (r.circuits or 0) > 0]

        buggy = False
        reason = ''
        if len(strong) >= 2:
            if all(r.circuits % r.qty == 0 and 1 <= r.circuits // r.qty <= 2000
                   for r in strong):
                buggy = True
                reason = f'{len(strong)} strong rows all divisible'
        elif len(strong) == 1:
            r = strong[0]
            if (r.circuits % r.qty == 0 and 1 <= r.circuits // r.qty <= 2000
                    and r.circuits > 2000):
                buggy = True
                reason = f'1 strong row, circuits={r.circuits} > 2000'

        if not buggy:
            if strong and not all(
                    r.circuits % r.qty == 0 for r in strong):
                pass  # correct file: per-unit values not divisible by qty
            elif len(strong) <= 1:
                skipped_ambiguous.append((src, len(rows), len(strong)))
            continue

        # verify & collect updates
        updates = []
        for r in qty_rows:
            if (r.circuits or 0) <= 0:
                continue
            if r.circuits % r.qty != 0:
                log(f'  WARN non-divisible row in flagged file: id={r.id} '
                    f'qty={r.qty} circuits={r.circuits}')
                continue
            new_c = r.circuits // r.qty
            new_t = r.circuits
            if new_t != r.qty * new_c:
                continue
            updates.append((r, new_c, new_t))

        log(f'FLAGGED: {src}  rows={len(rows)} qty_rows={len(qty_rows)} '
            f'-> update {len(updates)} rows ({reason})')
        sample = updates[:3]
        for r, nc, nt in sample:
            log(f'    id={r.id} part={r.part_no} qty={r.qty} '
                f'circuits {r.circuits} -> {nc}, total {r.total_circuits} -> {nt}')

        total_flagged_files += 1
        total_update_rows += len(updates)

        if APPLY:
            for r, nc, nt in updates:
                r.circuits = nc
                r.total_circuits = nt
            db.session.commit()

    log('=' * 70)
    log(f'flagged files: {total_flagged_files}, rows to update: {total_update_rows}')
    if skipped_ambiguous:
        log(f'ambiguous files (0-1 qty>=2 rows, left untouched; re-upload to fix): {len(skipped_ambiguous)}')
        for src, nrows, nstrong in skipped_ambiguous[:20]:
            log(f'    {src} (rows={nrows}, strong={nstrong})')
    log('APPLIED' if APPLY else 'DRY-RUN ONLY (use --apply to write)')

with open(r'f:\TA\bom3\_chk_backfill.txt', 'w', encoding='utf-8') as f:
    f.write(log_buf.getvalue())
