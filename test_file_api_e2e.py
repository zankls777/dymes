#!/usr/bin/env python3
"""
文件对比 API 端到端测试（数据库读取模式）
使用 Flask 测试客户端验证 /api/file_headers 和 /api/file_compare_multi
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app_factory import create_app, db
from models.file_info import FileInfo

PASS = 0
FAIL = 0


def check(name, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  ✅ {name}')
    else:
        FAIL += 1
        print(f'  ❌ {name} {detail}')


def login(client):
    """通过 /login POST 表单登录（走完整登录流程，含 session 持久化）"""
    # 先获取 CSRF token
    resp = client.get('/login')
    import re
    html = resp.get_data(as_text=True)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]*)"', html)
    csrf_token = m.group(1) if m else ''
    resp = client.post('/login', data={
        'username': 'admin',
        'password': 'admin123!',
        'csrf_token': csrf_token
    }, follow_redirects=True)
    return resp.status_code in (200, 302)


def main():
    print('=' * 60)
    print('文件对比 API 端到端测试（数据库读取模式）')
    print('=' * 60)

    app = create_app()
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['TESTING'] = True

    with app.app_context():
        # 找两个有业务数据的 SubList 文件
        files = FileInfo.query.filter_by(file_type='SubList').limit(3).all()
        print(f'\n[1] 准备测试文件: {len(files)} 个')

        valid = []
        for f in files:
            from models.sub_list import SubList
            cnt = SubList.query.filter(SubList.source_file == f.filename, SubList.deleted == False).count()
            if cnt > 0:
                valid.append((f, cnt))

        check('找到至少2个有业务数据的SubList文件', len(valid) >= 2, f'(found={len(valid)})')

        if len(valid) < 2:
            print('测试数据不足，跳过API测试')
            return 1

        client = app.test_client()
        # 登录（POST /login 完整流程，session 在测试客户端内持久化）
        check('登录成功', login(client))

        # [2] 测试 /api/file_headers
        print('\n[2] 测试 /api/file_headers')
        print('-' * 60)
        resp = client.post('/api/file_headers', json={
            'file_id_1': valid[0][0].id,
            'file_id_2': valid[1][0].id
        })
        check('HTTP 200', resp.status_code == 200, f'(status={resp.status_code})')
        if resp.status_code == 200:
            data = resp.get_json()
            check('success=true', data.get('success') is True)
            headers_list = data.get('headers_list') or []
            check('返回两个文件的表头', len(headers_list) == 2)
            if headers_list:
                check('表头包含 SubPno', 'SubPno' in headers_list[0])
                check('表头包含 Circuit', 'Circuit' in headers_list[0])
                print(f'  文件1表头数: {len(headers_list[0])}')
                print(f'  文件2表头数: {len(headers_list[1])}')

        # [3] 测试 /api/file_compare（关键列模式）
        print('\n[3] 测试 /api/file_compare (key模式)')
        print('-' * 60)
        resp = client.post('/api/file_compare', json={
            'file_id_1': valid[0][0].id,
            'file_id_2': valid[1][0].id,
            'key_column': 'SubPno',
            'compare_columns': ['Circuit', 'Wire', 'SetCode'],
            'compare_mode': 'key'
        })
        check('HTTP 200', resp.status_code == 200, f'(status={resp.status_code})')
        if resp.status_code == 200:
            data = resp.get_json()
            check('success=true', data.get('success') is True)
            summary = data.get('summary') or data.get('result') or {}
            print(f'  对比结果摘要: {json.dumps(summary, ensure_ascii=False)[:300]}')

        # [4] 测试 /api/file_compare_multi
        print('\n[4] 测试 /api/file_compare_multi')
        print('-' * 60)
        resp = client.post('/api/file_compare_multi', json={
            'baseline_file_id': valid[0][0].id,
            'file_ids': [valid[0][0].id, valid[1][0].id]
        })
        check('HTTP 200', resp.status_code == 200, f'(status={resp.status_code})')
        if resp.status_code == 200:
            data = resp.get_json()
            check('success=true', data.get('success') is True)
            print(f'  多文件对比响应字段: {list(data.keys())}')

        # [5] 测试文件下载（从数据库生成Excel）
        print('\n[5] 测试 /download/<filename> (从数据库生成)')
        print('-' * 60)
        resp = client.get(f"/download/{valid[0][0].filename}")
        check('HTTP 200', resp.status_code == 200, f'(status={resp.status_code})')
        if resp.status_code == 200:
            content = resp.data
            check('返回Excel内容', len(content) > 1000, f'(size={len(content)})')
            # xlsx 文件以 PK 开头（zip格式）
            check('有效xlsx格式(PK头)', content[:2] == b'PK')

    print('\n' + '=' * 60)
    print(f'测试结果: 通过 {PASS} 项, 失败 {FAIL} 项')
    print('=' * 60)
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
