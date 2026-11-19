from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time


class WireCrimpLogin(db.Model):
    """压高登陆 - 用户经 Excel 维护的压高表

    匹配键：回路号 + 号机(去$编号) + 线质 + 颜色 + 规格 + 端子(双端子组合) 全部一致时，
    扫描录入页才拉取该压高显示；不存在匹配时不显示压高。

    与拍照识别知识库(wire_label_crimp)解耦：此处压高完全由用户上传维护，
    不再依赖拍照 OCR 或 SubList 行号锚点。
    """
    __tablename__ = 'wire_crimp_login'
    __table_args__ = (
        # 匹配查询索引：按回路号+号机+线质+颜色快速定位候集
        Index('idx_wcl_circuit_machine', 'circuit_no', 'machine_no'),
        Index('idx_wcl_lot', 'lot_no'),
    )

    id = db.Column(db.Integer, primary_key=True)

    # —— 匹配键（文本固化，宽松归一化比对在查询侧做）——
    lot_no = db.Column(db.String(100), nullable=True)        # LOT（可选，仅作来源追溯，不参与匹配）
    circuit_no = db.Column(db.String(100), nullable=False)   # 回路号
    machine_no = db.Column(db.String(100), nullable=False)   # 号机名（已去$编号，如 M-15）
    wire = db.Column(db.String(50), nullable=False, default='')  # 线质（如 AVSS/CIVUS）
    color = db.Column(db.String(50), nullable=False)         # 颜色
    sq = db.Column(db.String(50), nullable=False)            # 规格
    tml1 = db.Column(db.String(100), nullable=False)         # 端子名1（保留原始值，含 -；匹配时归一化）
    tml2 = db.Column(db.String(100), nullable=False)         # 端子名2（保留原始值，含 -；单端子时与 tml1 相同）

    # —— 压高值 ——
    strip1 = db.Column(db.String(50), nullable=False)         # 压高1（对应端子1）
    strip2 = db.Column(db.String(50), nullable=False)         # 压高2（对应端子2）

    # —— 来源 / 审计 ——
    source_file = db.Column(db.String(200), nullable=True)   # 上传文件名
    created_by = db.Column(db.Integer, nullable=True)         # 上传操作人
    # 修正人员姓名快照：上传/修正该条压高的人（用户删除后仍可显示文本）
    created_by_name = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=get_utc8_time)
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)

    def __repr__(self):
        return f'<WireCrimpLogin {self.circuit_no}/{self.machine_no}/{self.color}/{self.sq}>'

    def to_dict(self):
        return {
            'id': self.id,
            'lot_no': self.lot_no or '',
            'circuit_no': self.circuit_no or '',
            'machine_no': self.machine_no or '',
            'wire': self.wire or '',
            'color': self.color or '',
            'sq': self.sq or '',
            'tml1': self.tml1 or '',
            'tml2': self.tml2 or '',
            'strip1': self.strip1 or '',
            'strip2': self.strip2 or '',
            'source_file': self.source_file or '',
            'created_by': self.created_by,
            'created_by_name': self.created_by_name or '',
            'created_at': self.created_at.isoformat() if self.created_at else '',
            'updated_at': self.updated_at.isoformat() if self.updated_at else '',
        }
