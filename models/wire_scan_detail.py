from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time


class WireScanDetail(db.Model):
    """补线打端子 - 扫描时刻快照的线束明细（文本存储）

    扫码拉取到 SubList 资料后，立即以纯文本形式固化到本表（按 LOT+回路号 分组，
    seq 为同回路内行序）。此后数据下载/Excel 导出直接读本表，
    不再实时条件查询 sub_list；即使原始资料被删除或冻结归档，
    已扫描的数据仍完整保留、可查可下载。
    """
    __tablename__ = 'wire_scan_detail'
    __table_args__ = (
        Index('idx_wsd_lot_circuit', 'lot_no', 'circuit_no'),
        Index('idx_wsd_part_eo_circuit', 'part_no', 'jx_eo', 'circuit_no'),
    )

    id = db.Column(db.Integer, primary_key=True)
    lot_no = db.Column(db.String(100), nullable=False)       # LOT
    circuit_no = db.Column(db.String(100), nullable=True)    # 回路号
    part_no = db.Column(db.String(100), nullable=True)       # 品番（快照）
    jx_eo = db.Column(db.String(50), nullable=True)          # 京信EO（快照）
    seq = db.Column(db.Integer, default=0)                   # 同回路内行序（按 sub_list.id）

    # ---- 以下全部为扫描时刻从原始资料固化的文本，不再随原始资料变化 ----
    wire = db.Column(db.String(50))                          # 线质
    color = db.Column(db.String(50))                         # 颜色
    sq = db.Column(db.String(50))                            # 规格
    length_cut2 = db.Column(db.String(50))                   # 长度
    tml1 = db.Column(db.String(50))                          # 端子名1
    tml2 = db.Column(db.String(50))                          # 端子名2
    rel1 = db.Column(db.String(50))
    rel2 = db.Column(db.String(50))
    seal1 = db.Column(db.String(50))
    seal2 = db.Column(db.String(50))
    strip1 = db.Column(db.String(50))                        # 压高/剥线1
    strip2 = db.Column(db.String(50))                        # 压高/剥线2
    remark1 = db.Column(db.String(200))                      # 备注/配线区分
    is_frozen = db.Column(db.Boolean, default=False, nullable=False)  # 快照时资料是否冻结归档

    created_at = db.Column(db.DateTime, default=get_utc8_time)
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)

    def __repr__(self):
        return f'<WireScanDetail {self.lot_no}/{self.circuit_no}#{self.seq}>'

    def to_dict(self):
        return {
            'circuit': self.circuit_no,
            # 保留快照行固化的品番/EO，供下载/录入页「兼容键」逻辑读取
            # （LOT 登录资料后来变更时仍需按快照原品番/EO 命中）
            'part_no': self.part_no or '', 'jx_eo': self.jx_eo or '',
            'wire': self.wire or '', 'color': self.color or '', 'sq': self.sq or '',
            'length_cut2': self.length_cut2 or '',
            'tml1': self.tml1 or '', 'tml2': self.tml2 or '',
            'rel1': self.rel1 or '', 'rel2': self.rel2 or '',
            'seal1': self.seal1 or '', 'seal2': self.seal2 or '',
            'strip1': self.strip1 or '', 'strip2': self.strip2 or '',
            'remark1': self.remark1 or '',
            'frozen': bool(self.is_frozen),
        }
