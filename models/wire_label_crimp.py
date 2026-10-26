from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time


class WireLabelCrimp(db.Model):
    """拍照识别的标签压高知识库（按图纸复用）

    sub_list.Strip1/Strip2 存的是标签行号锚点（如 4.1/4.3），并非真实 CCH 压高；
    真实压高只印在标签照片上。拍照识别后把真实 CCH 压高登记到本表。

    复用规则：压高是端子的物理属性，同一图纸管理编号（标签 SET: 后的值）下、
    同一对端子的压高一致。因此以 (图纸管理编号, 端子1, 端子2) 为唯一键：
    - 本次照片识别到压高：更新/登记该键的压高；
    - 本次照片识别不到压高：不新增压高，直接调取该键的历史记录应用；
    - 照片连图纸编号都未识别时，退化为按 (LOT, 回路, 端子对) 取最近来源记录。
    lot_no/circuit_no 仅记录「最近一次拍照来源」，供审计与无图纸编号时兜底查询。
    """
    __tablename__ = 'wire_label_crimp'
    __table_args__ = (
        # 同一图纸 + 同一对端子仅保留一条压高知识（NULL 不参与唯一约束，
        # 图纸编号未识别时各行不冲突，由应用层按 LOT+回路兜底定位）
        Index('uk_wlc_drawing_tml', 'drawing_mgmt_no', 'tml1', 'tml2',
              unique=True),
        Index('idx_wlc_lot_circuit', 'lot_no', 'circuit_no'),
    )

    id = db.Column(db.Integer, primary_key=True)
    lot_no = db.Column(db.String(100), nullable=False)       # 最近来源 LOT
    circuit_no = db.Column(db.String(100), nullable=False)   # 最近来源回路号

    # ---- 复用主键：图纸管理编号 + 端子对 ----
    drawing_mgmt_no = db.Column(db.String(100), nullable=True)  # SET: 后的图纸管理编号
    tml1 = db.Column(db.String(50), default='')              # 端子1（KC 侧）
    tml2 = db.Column(db.String(50), default='')              # 端子2（KT 侧）

    # ---- 压高知识：标签实测 CCH（两端）----
    strip1 = db.Column(db.String(50), default='')            # 端子1 压高，如 1.05-1.15
    strip2 = db.Column(db.String(50), default='')            # 端子2 压高，如 0.95-1.05

    # ---- 标签携带的附加信息（审计/核对用，非匹配键）----
    part_no = db.Column(db.String(100))                      # 品番
    jx_eo = db.Column(db.String(50))                         # EO 版本
    machine_no = db.Column(db.String(50))                    # 号机完整编码 M-xx$编号
    cut_no = db.Column(db.String(50))                        # CUT 编号

    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=get_utc8_time)
    updated_at = db.Column(db.DateTime, default=get_utc8_time,
                           onupdate=get_utc8_time)

    def __repr__(self):
        return f'<WireLabelCrimp {self.drawing_mgmt_no or self.lot_no}/{self.tml1}/{self.tml2}>'
