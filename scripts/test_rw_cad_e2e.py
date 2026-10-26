"""RW图纸产出 端到端测试：多选上传 → 批量生成 → 下载。"""
import requests
import re

BASE = 'http://127.0.0.1:5004'
DIFF = r'F:\TA\bom3\template\RW对比差异_918G0-R0050_c5_vs_918G8-R0650_c5_20260928_081454.xlsx'
DXF_RW = r'F:\TA\bom3\tmp_dxf\274360.29_918G8-R0650.dxf'
DXF_BASE = r'F:\TA\bom3\tmp_dxf\274360.29_918G0-R0050.dxf'

s = requests.Session()

# 1. 登录
r = s.get(BASE + '/login')
m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.text)
token = m.group(1) if m else ''
r = s.post(BASE + '/login', data={'username': 'admin', 'password': 'admin123!', 'csrf_token': token})
print('登录:', r.status_code, r.url)

# 2. 页面
r = s.get(BASE + '/rw_cad')
print('页面:', r.status_code, '含标题:', 'RW图纸产出' in r.text)
m = re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text)
hdr = {'X-CSRFToken': m.group(1)} if m else {}

# 3. 一次上传 2 份差异文件（模拟多选，同一份传两次）
with open(DIFF, 'rb') as f1, open(DIFF, 'rb') as f2:
    r = s.post(BASE + '/api/rw_cad/upload_diff',
               files=[('files', ('diff_a.xlsx', f1)), ('files', ('diff_b.xlsx', f2))], headers=hdr)
print('上传差异(2份):', r.status_code, [(x['filename'], x['summary']) for x in r.json().get('results', [])])
diff_ids = [x['file_id'] for x in r.json().get('results', [])]

# 4. 一次上传 2 份图纸（多选）
with open(DXF_RW, 'rb') as f1, open(DXF_BASE, 'rb') as f2:
    r = s.post(BASE + '/api/rw_cad/upload',
               files=[('files', ('274360.29_918G8-R0650.dxf', f1)),
                      ('files', ('274360.29_918G0-R0050.dxf', f2))], headers=hdr)
print('上传图纸(2份):', r.status_code, [x['display'] for x in r.json().get('results', [])])
file_ids = [x['file_id'] for x in r.json().get('results', [])]

# 5. 批量生成（2 份差异 + 2 份图纸）
r = s.post(BASE + '/api/rw_cad/generate',
           json={'diff_ids': diff_ids, 'file_ids': file_ids}, headers=hdr)
data = r.json()
print('批量生成:', r.status_code, '成功', len(data.get('results', [])), '份')
for x in data.get('results', []):
    print('  -', x['pno'], '增', x['add_marks'], '处/删', x['del_marks'], '处', '->', x['filename'])

# 6. 下载第一份
if data.get('results'):
    r = s.get(BASE + '/api/rw_cad/download/' + data['results'][0]['file_id'])
    print('下载:', r.status_code, '字节:', len(r.content))

# 7. 列表
r = s.get(BASE + '/api/rw_cad/list')
d = r.json()
print('差异文件:', len(d.get('diffs', [])), '份；图纸:', len(d.get('files', [])), '份')
