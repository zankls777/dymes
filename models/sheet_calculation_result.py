# models/sheet_calculation_result.py
from db import db
from datetime import datetime

class SheetCalculationResult(db.Model):
    """皮子换算结果模型"""
    __tablename__ = 'sheet_calculation_result'
    __table_args__ = (
        db.Index('idx_sheet_result_user_id', 'user_id'),
        db.Index('idx_sheet_result_created_at', 'created_at'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    user_name = db.Column(db.String(50), nullable=False)  # 操作人姓名
    result_data = db.Column(db.Text, nullable=False)  # JSON格式存储计算结果
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_name': self.user_name,
            'result_data': self.result_data,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
