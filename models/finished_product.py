from db import db
from datetime import datetime

class FinishedProduct(db.Model):
    """成品数据模型"""
    __tablename__ = 'finished_product'
    __table_args__ = (
        db.UniqueConstraint('lot_number', name='uq_finished_product_lot'),
        db.Index('idx_finished_product_part', 'part_number'),
        db.Index('idx_finished_product_vehicle', 'vehicle_name'),
        db.Index('idx_finished_product_lot', 'lot_number'),
        db.Index('idx_finished_product_update', 'update_time'),
    )
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vehicle_name = db.Column(db.String(100), nullable=False, comment='车种')
    part_number = db.Column(db.String(100), nullable=False, comment='品番')
    lot_number = db.Column(db.String(100), nullable=False, unique=True, comment='LOT号')
    quantity = db.Column(db.Integer, nullable=False, default=0, comment='数量')
    # EO 相关字段
    eo = db.Column(db.String(100), comment='EO')
    eo_override = db.Column(db.Boolean, default=False, comment='EO是否被手动覆盖')
    usage_eo = db.Column(db.String(100), comment='UsageList中的EO')
    needs_eo_attention = db.Column(db.Boolean, default=False, comment='是否需要关注EO')
    remark = db.Column(db.Text, comment='备注')
    source = db.Column(db.String(20), default='manual', comment='数据来源：manual-手动添加，excel-Excel导入')
    create_time = db.Column(db.DateTime, default=datetime.now, comment='创建时间')
    update_time = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')
    # 操作人相关字段
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), comment='创建者ID')
    updated_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), comment='修改者ID')
    # 操作人姓名快照：账号删除后仍可显示历史文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='创建人姓名快照')
    updated_by_name = db.Column(db.String(80), nullable=True, comment='修改人姓名快照')
    
    # 关联用户信息
    # 性能优化：selectin 批量预取，避免 to_dict 逐行反查用户
    creator = db.relationship('UserInfo', foreign_keys=[created_by], lazy='selectin', backref=db.backref('created_products', lazy='dynamic'))
    modifier = db.relationship('UserInfo', foreign_keys=[updated_by], lazy='selectin', backref=db.backref('modified_products', lazy='dynamic'))

    def _resolve_user_name(self, user_id, snapshot, operator_names=None):
        """解析操作人姓名：优先实时反查，用户已删除（查不到）时回退快照文本。

        性能优化：支持传入预取的 operator_names 映射 {user_id: username}，
        批量序列化时一次性查库，避免逐行 N+1 查询。
        """
        if user_id:
            if operator_names is not None:
                name = operator_names.get(user_id)
                if name:
                    return name
            else:
                from models.user_info import UserInfo
                user = db.session.get(UserInfo, user_id)
                if user:
                    return user.username
        return snapshot
    
    @property
    def created_by_name_display(self):
        """创建者名称（含删除账号的回退快照）"""
        return self._resolve_user_name(self.created_by, self.created_by_name)
    
    @property
    def updated_by_name_display(self):
        """修改者名称（含删除账号的回退快照）"""
        return self._resolve_user_name(self.updated_by, self.updated_by_name)
    
    @classmethod
    def build_operator_names(cls, products):
        """批量为成品列表构建 {user_id: username} 映射，供 to_dict 复用。

        避免每个成品在 to_dict 里各查一次用户表（N+1）。
        """
        from models.user_info import UserInfo
        ids = set()
        for p in products:
            if p.created_by:
                ids.add(p.created_by)
            if p.updated_by:
                ids.add(p.updated_by)
        if not ids:
            return {}
        rows = db.session.query(UserInfo.id, UserInfo.username).filter(UserInfo.id.in_(ids)).all()
        return {uid: uname for uid, uname in rows}
    
    def to_dict(self, operator_names=None):
        """转换为字典格式

        :param operator_names: 可选，预取的 {user_id: username} 映射。
                               传入则不再逐行查库（批量场景必传）。
        """
        return {
            'id': self.id,
            'vehicle_name': self.vehicle_name,
            'part_number': self.part_number,
            'lot_number': self.lot_number,
            'eo': self.eo,
            'eo_override': self.eo_override,
            'usage_eo': self.usage_eo,
            'needs_eo_attention': self.needs_eo_attention,
            'quantity': self.quantity,
            'remark': self.remark,
            'source': self.source,
            'create_time': self.create_time.strftime('%Y-%m-%d %H:%M:%S') if self.create_time else None,
            'update_time': self.update_time.strftime('%Y-%m-%d %H:%M:%S') if self.update_time else None,
            'created_by': self.created_by,
            'updated_by': self.updated_by,
            # 账号已删除时回退到写入时的姓名快照，避免显示为「未知」
            'created_by_name': self._resolve_user_name(self.created_by, self.created_by_name, operator_names) or '未知',
            'updated_by_name': self._resolve_user_name(self.updated_by, self.updated_by_name, operator_names) or '未知'
        }
    
    def get_display_eo(self):
        """下载/展示时的EO取值规则：优先取录入的EO，否则回退到UsageList的EO。"""
        try:
            if self.eo:
                return self.eo
            if self.usage_eo:
                return self.usage_eo
            return ''
        except Exception:
            return ''
    
    def __repr__(self):
        return f'<FinishedProduct {self.lot_number}>'


def get_finished_product_model():
    """获取成品模型类，用于避免循环导入问题"""
    return FinishedProduct
