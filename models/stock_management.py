# -*- coding: utf-8 -*-
"""在库管理与出入库管理数据模型

业务关系：
    StockItem（在库主数据，资材品番唯一）
        └─ StockMovement（出入库流水，一条流水对应一个品番的一次入库/出库）

数量口径：
    剩余量 = 初始数量 + 入库数量 - 出库数量
    - initial_qty  : 建账时的期初数量，可在「在库管理」直接维护
    - inbound_qty  : 累计入库数量（在库管理可手工调整 + 出入库页面自动累加）
    - outbound_qty : 累计出库数量（同上）
    - balance_qty  : 只读计算值，不落库，避免与流水产生偏差
"""
from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time

# 出入库类型
MOVEMENT_IN = 'in'      # 入库
MOVEMENT_OUT = 'out'    # 出库
MOVEMENT_TYPE_LABELS = {MOVEMENT_IN: '入库', MOVEMENT_OUT: '出库'}


class StockItem(db.Model):
    """在库主数据（资材品番维度）"""
    __tablename__ = 'stock_item'
    __table_args__ = (
        Index('idx_stock_item_pno', 'ksc_pno', unique=True),
        Index('idx_stock_item_corp', 'corp_pno'),
        Index('idx_stock_item_location', 'location'),
    )

    id = db.Column(db.Integer, primary_key=True)
    ksc_pno = db.Column(db.String(50), nullable=False, unique=True, comment='资材品番')
    corp_pno = db.Column(db.String(100), nullable=True, comment='业体品番')
    part_name = db.Column(db.String(200), nullable=True, comment='品名')
    location = db.Column(db.String(100), nullable=True, comment='位置')
    initial_qty = db.Column(db.Integer, default=0, nullable=False, comment='初始数量')
    inbound_qty = db.Column(db.Integer, default=0, nullable=False, comment='累计入库数量')
    outbound_qty = db.Column(db.Integer, default=0, nullable=False, comment='累计出库数量')
    remark = db.Column(db.String(500), nullable=True, comment='备注')

    created_at = db.Column(db.DateTime, default=get_utc8_time)
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    created_by_name = db.Column(db.String(80), nullable=True, comment='创建人姓名快照')
    updated_by_name = db.Column(db.String(80), nullable=True, comment='最后操作人姓名快照')

    @property
    def balance_qty(self):
        """剩余量：初始 + 入库 - 出库（允许负库存）"""
        return (int(self.initial_qty or 0)
                + int(self.inbound_qty or 0)
                - int(self.outbound_qty or 0))

    def __repr__(self):
        return f'<StockItem {self.ksc_pno} {self.balance_qty}>'

    def to_dict(self):
        return {
            'id': self.id,
            'ksc_pno': self.ksc_pno or '',
            'corp_pno': self.corp_pno or '',
            'part_name': self.part_name or '',
            'location': self.location or '',
            'initial_qty': int(self.initial_qty or 0),
            'inbound_qty': int(self.inbound_qty or 0),
            'outbound_qty': int(self.outbound_qty or 0),
            'balance_qty': self.balance_qty,
            'remark': self.remark or '',
            'created_by_name': self.created_by_name or '',
            # 页面展示用：优先最后操作人，旧数据回退到创建人
            'operator_name': self.updated_by_name or self.created_by_name or '',
            'updated_at': format_utc8_time(self.updated_at) if self.updated_at else None,
        }


class StockMovement(db.Model):
    """出入库流水

    一次提交（多条品番）共享同一个 batch_no，便于按批次追溯。
    班组信息保存名称快照，班组改名/停用后历史记录仍可读。
    """
    __tablename__ = 'stock_movement'
    __table_args__ = (
        Index('idx_stock_movement_date', 'movement_date'),
        Index('idx_stock_movement_pno', 'ksc_pno'),
        Index('idx_stock_movement_date_type', 'movement_date', 'movement_type'),
        Index('idx_stock_movement_batch', 'batch_no'),
    )

    id = db.Column(db.Integer, primary_key=True)
    movement_date = db.Column(db.Date, nullable=False, comment='出入库日期')
    movement_type = db.Column(db.String(10), nullable=False, comment='in=入库，out=出库')
    team_id = db.Column(db.Integer, db.ForeignKey('inventory_team.id'), nullable=True)
    team_name = db.Column(db.String(50), nullable=True, comment='班组名称快照')
    stock_item_id = db.Column(db.Integer, db.ForeignKey('stock_item.id'), nullable=True)
    ksc_pno = db.Column(db.String(50), nullable=False, comment='资材品番')
    part_name = db.Column(db.String(200), nullable=True, comment='品名快照')
    location = db.Column(db.String(100), nullable=True, comment='位置快照')
    qty = db.Column(db.Integer, default=0, nullable=False, comment='本次数量')
    balance_after = db.Column(db.Integer, default=0, nullable=False, comment='操作后剩余量快照')
    remark = db.Column(db.String(500), nullable=True, comment='备注')
    batch_no = db.Column(db.String(40), nullable=True, comment='提交批次号')

    created_at = db.Column(db.DateTime, default=get_utc8_time)
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    created_by_name = db.Column(db.String(80), nullable=True, comment='操作人姓名快照')

    team = db.relationship('InventoryTeam', foreign_keys=[team_id])
    item = db.relationship('StockItem', foreign_keys=[stock_item_id])

    def __repr__(self):
        return f'<StockMovement {self.movement_date} {self.movement_type} {self.ksc_pno} {self.qty}>'

    def to_dict(self):
        return {
            'id': self.id,
            'movement_date': self.movement_date.strftime('%Y-%m-%d') if self.movement_date else None,
            'movement_type': self.movement_type,
            'movement_type_label': MOVEMENT_TYPE_LABELS.get(self.movement_type, self.movement_type),
            'team_id': self.team_id,
            'team_name': self.team_name or '',
            'stock_item_id': self.stock_item_id,
            'ksc_pno': self.ksc_pno or '',
            'part_name': self.part_name or '',
            'location': self.location or '',
            'qty': int(self.qty or 0),
            'balance_after': int(self.balance_after or 0),
            'remark': self.remark or '',
            'batch_no': self.batch_no or '',
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'created_by_name': self.created_by_name or '',
        }
