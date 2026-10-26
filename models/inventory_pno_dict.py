from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time


class InventoryPnoDict(db.Model):
    """资材品番字典表

    从 usage_list.KscPno 去重抽取的资材品番主数据（约 1.4 千条），
    供「盘点实查」页面2的品番选择器快速检索，避免每次扫描 53 万行的 usage_list。

    - 仅在 usage_list 出现新规品番时增量补入（由 sync_from_usage_list 完成）
    - 无对应 Part_Name 的脏数据不入库
    """
    __tablename__ = 'inventory_pno_dict'
    __table_args__ = (
        Index('idx_inventory_pno_dict_pno', 'ksc_pno', unique=True),
    )

    id = db.Column(db.Integer, primary_key=True)
    ksc_pno = db.Column(db.String(50), nullable=False, unique=True)   # 资材品番（去重）
    part_name = db.Column(db.String(200), nullable=True)             # 品名
    created_at = db.Column(db.DateTime, default=get_utc8_time)       # 首次入库时间（UTC+8）
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)

    def __repr__(self):
        return f'<InventoryPnoDict {self.ksc_pno}>'

    def to_dict(self):
        return {
            'ksc_pno': self.ksc_pno,
            'part_name': self.part_name or '',
            'updated_at': format_utc8_time(self.updated_at) if self.updated_at else None,
        }

    @staticmethod
    def sync_from_usage_list(app=None):
        """从 usage_list 增量同步资材品番（仅补入新规品番，更新品名变化）

        返回 (新增数, 更新数, 总条数)。

        M-5 优化：不再对整表 53 万行做 GROUP BY，而是利用 usage_list 自增主键
        `id` 记录上次同步水位（存于 system_setting），仅聚合 `id > 水位` 的新增行，
        使重复调用（每次上传后触发）成本与新增量成正比而非与总量成正比。

        兜底：当字典表为空（首次部署或表被清空）时，忽略水位做一次全量同步，
        避免水位已推进但字典被清空后无法自愈。
        """
        from models.usage_list import UsageList
        from models.system_setting import SystemSetting

        # 读取上次同步水位（usage_list 自增主键 id），默认 0 = 首次全量
        watermark_key = 'inventory_pno_dict_last_id'
        row = SystemSetting.query.filter_by(key=watermark_key).first()
        last_id = int(row.value) if (row is not None and (row.value or '').isdigit()) else 0

        # 字典表为空时强制全量（水位失效），保证可自愈
        dict_empty = InventoryPnoDict.query.first() is None
        if dict_empty:
            last_id = 0

        rows = db.session.query(
            UsageList.KscPno, db.func.max(UsageList.Part_Name), db.func.max(UsageList.id)
        ).filter(
            UsageList.id > last_id,                                    # 仅新增行
            UsageList.KscPno.isnot(None), UsageList.KscPno != '',
            UsageList.Part_Name.isnot(None), UsageList.Part_Name != '',
            db.or_(UsageList.deleted.is_(None), UsageList.deleted == False),  # noqa: E712
            db.or_(UsageList.is_frozen.is_(None), UsageList.is_frozen == False),  # noqa: E712
        ).group_by(UsageList.KscPno).all()

        # 即使无新增行，也要推进水位（避免下次重复扫描已处理区间）
        max_seen_id = max([r[2] for r in rows if r[2] is not None], default=last_id)

        if not rows:
            total = InventoryPnoDict.query.count()
            InventoryPnoDict._advance_watermark(watermark_key, max_seen_id)
            db.session.commit()
            return 0, 0, total

        src = {(r[0] or '').strip(): (r[1] or '').strip() for r in rows if (r[0] or '').strip()}
        existing = {d.ksc_pno: d for d in InventoryPnoDict.query.all()}

        added = updated = 0
        for pno, pname in src.items():
            d = existing.get(pno)
            if d is None:
                db.session.add(InventoryPnoDict(ksc_pno=pno, part_name=pname))
                added += 1
            elif (d.part_name or '') != pname:
                d.part_name = pname
                updated += 1

        # 推进水位并持久化（与业务变更同一次提交，保证原子性）
        InventoryPnoDict._advance_watermark(watermark_key, max_seen_id)
        db.session.commit()

        if app is not None and (added or updated):
            app.logger.info(f"资材品番字典同步: 新增={added}, 更新={updated}, 总源={len(src)}")
        return added, updated, len(src)

    @staticmethod
    def _advance_watermark(key, new_id):
        """推进并持久化同步水位（幂等；并发安全）

        写入时按 key 重新查询，避免调用方读取的 row 在提交前已过期；
        以 SAVEPOINT 包裹插入，并发插入冲突（唯一约束）时忽略，由对方写入。
        """
        from models.system_setting import SystemSetting
        from sqlalchemy.exc import IntegrityError

        cur = SystemSetting.query.filter_by(key=key).first()
        if cur is None:
            try:
                with db.session.begin_nested():  # SAVEPOINT
                    db.session.add(SystemSetting(
                        key=key, value=str(new_id),
                        description='资材品番字典同步水位(usage_list.id)'))
            except IntegrityError:
                # 并发线程已插入同一键，回退后按更新处理
                cur = SystemSetting.query.filter_by(key=key).first()
                if cur is not None and int(cur.value or 0) < new_id:
                    cur.value = str(new_id)
        elif int(cur.value or 0) < new_id:
            cur.value = str(new_id)
