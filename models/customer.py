from db import db
from utils.time_utils import get_utc8_time, format_utc8_time


class Customer(db.Model):
    """客户主数据

    供计划登录、单价登陆等页面的「客户」下拉选择使用。
    """
    __tablename__ = 'customer'

    id = db.Column(db.Integer, primary_key=True)
    cust_code = db.Column(db.String(50), nullable=False, unique=True)   # 客户代码
    cust_name = db.Column(db.String(100), nullable=False)               # 客户名称
    sort_no = db.Column(db.Integer, default=0)                          # 排序号
    is_active = db.Column(db.Boolean, default=True)                     # 是否启用
    remark = db.Column(db.String(200), nullable=True)                   # 备注

    created_at = db.Column(db.DateTime, default=get_utc8_time)
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    # 创建人姓名快照：账号删除后仍可显示历史文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='创建人姓名快照')
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)

    creator = db.relationship('UserInfo', foreign_keys=[created_by],
                              backref=db.backref('customers', lazy='dynamic'))

    def __repr__(self):
        return f'<Customer {self.cust_code} {self.cust_name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'cust_code': self.cust_code or '',
            'cust_name': self.cust_name or '',
            'sort_no': self.sort_no or 0,
            'is_active': bool(self.is_active),
            'remark': self.remark or '',
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'updated_at': format_utc8_time(self.updated_at) if self.updated_at else None,
            'created_by': self.created_by,
            # 优先运行时关联用户名；账号已删除时回退写入时快照
            'created_by_name': (self.creator.username if self.creator else None) or self.created_by_name or '',
        }
