from app_factory import create_app
app = create_app()

with app.app_context():
    from db import db
    from models.tape_calculation_result import TapeCalculationResult
    print('模型导入成功')
    print('表名:', TapeCalculationResult.__tablename__)
    db.create_all()
    print('表创建成功')
