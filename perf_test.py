#!/usr/bin/env python3
"""
冻结数据性能测试脚本
测量关键页面响应时间，验证优化效果
"""
import urllib.request
import urllib.parse
import time
import json
import http.cookiejar
import sys

BASE_URL = 'http://localhost:5004'
USERNAME = 'admin'
PASSWORD = 'admin123!'

def create_session():
    """创建带cookie的session"""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    return opener

def login(opener):
    """登录并获取session"""
    # 先获取CSRF token
    try:
        resp = opener.open(f'{BASE_URL}/login', timeout=10)
        html = resp.read().decode('utf-8')
        # 提取csrf_token
        import re
        match = re.search(r'name="csrf_token"[^>]*value="([^"]*)"', html)
        csrf_token = match.group(1) if match else ''
    except Exception as e:
        print(f'获取登录页失败: {e}')
        return False

    # 提交登录
    data = urllib.parse.urlencode({
        'username': USERNAME,
        'password': PASSWORD,
        'csrf_token': csrf_token
    }).encode()
    req = urllib.request.Request(f'{BASE_URL}/login', data=data, method='POST')
    try:
        resp = opener.open(req, timeout=10)
        return resp.status == 200
    except urllib.error.HTTPError as e:
        # 302重定向视为登录成功
        return e.code in (302, 200)
    except Exception as e:
        print(f'登录失败: {e}')
        return False

def measure_page(opener, url, label, iterations=3):
    """测量页面响应时间"""
    times = []
    for i in range(iterations):
        start = time.perf_counter()
        try:
            resp = opener.open(url, timeout=30)
            content = resp.read()
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            size_kb = len(content) / 1024
            status = resp.status
        except Exception as e:
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            size_kb = 0
            status = f'ERR: {e}'
    avg = sum(times) / len(times)
    min_t = min(times)
    max_t = max(times)
    print(f'  {label:30s} | avg={avg*1000:8.1f}ms | min={min_t*1000:8.1f}ms | max={max_t*1000:8.1f}ms | status={status} | size={size_kb:.1f}KB')
    return {'label': label, 'avg_ms': avg*1000, 'min_ms': min_t*1000, 'max_ms': max_t*1000, 'status': status}

def main():
    print('=' * 80)
    print('冻结数据性能测试')
    print('=' * 80)

    opener = create_session()

    # 登录
    print('\n[1] 登录...')
    if not login(opener):
        print('登录失败，退出测试')
        sys.exit(1)
    print('  登录成功')

    # 测试关键页面
    print('\n[2] 测量关键页面响应时间（3次取平均）:')
    print('-' * 80)

    results = []
    # SubList 页面（首次访问 - 冷缓存）
    results.append(measure_page(opener, f'{BASE_URL}/sub_list', 'SubList 首次(冷缓存)', 1))
    # SubList 页面（后续访问 - 热缓存）
    results.append(measure_page(opener, f'{BASE_URL}/sub_list', 'SubList 热缓存', 3))
    # SubList 翻页
    results.append(measure_page(opener, f'{BASE_URL}/sub_list?page=2&per_page=10', 'SubList 翻页(page=2)', 3))
    # SubList 翻页 - 更深
    results.append(measure_page(opener, f'{BASE_URL}/sub_list?page=5&per_page=10', 'SubList 翻页(page=5)', 3))

    # UsageList 页面
    results.append(measure_page(opener, f'{BASE_URL}/usage_list', 'UsageList 首次(冷缓存)', 1))
    results.append(measure_page(opener, f'{BASE_URL}/usage_list', 'UsageList 热缓存', 3))
    results.append(measure_page(opener, f'{BASE_URL}/usage_list?page=2&per_page=10', 'UsageList 翻页(page=2)', 3))

    # 文件管理页面
    results.append(measure_page(opener, f'{BASE_URL}/file_management', '文件管理', 3))

    # 成品页面
    results.append(measure_page(opener, f'{BASE_URL}/products', '成品列表', 3))

    # SubList 搜索（精确匹配 - 短字段）
    results.append(measure_page(opener, f'{BASE_URL}/sub_list?SubPno=G6550', 'SubList 搜索(精确)', 3))

    # UsageList 搜索
    results.append(measure_page(opener, f'{BASE_URL}/usage_list?SubPno=G6550', 'UsageList 搜索(精确)', 3))

    print('-' * 80)

    # 汇总
    print('\n[3] 测试汇总:')
    print(f'  总测试项: {len(results)}')
    avg_time = sum(r['avg_ms'] for r in results) / len(results)
    print(f'  平均响应时间: {avg_time:.1f}ms')

    # 保存结果
    with open('perf_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'\n  结果已保存到 perf_results.json')

    print('\n' + '=' * 80)
    print('测试完成')
    print('=' * 80)

if __name__ == '__main__':
    main()
