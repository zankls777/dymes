# db.py
from flask_sqlalchemy import SQLAlchemy

# 性能优化：expire_on_commit=False
# 默认 True 时，每次 commit 后访问对象属性都会触发一次 SELECT 刷新整行；
# 关闭后 commit 不再使对象过期，避免"写后回读"场景的冗余查询。
db = SQLAlchemy(session_options={'expire_on_commit': False})