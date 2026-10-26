#!/usr/bin/env python3
"""
性能测试脚本
用于测试不同数据量级下的页面加载时间和响应速度
"""

import requests
import time
import json
import csv
import os
from datetime import datetime

# 测试配置
BASE_URL = 'http://localhost:5004'
TEST_ENDPOINTS = [
    '/user_management',
    '/material_calculator/tape',
    '/operation_logs'
]
TEST_DATA_SIZES = [1000, 5000, 10000]
TEST_ITERATIONS = 3

# 测试结果保存路径
RESULTS_DIR = 'performance_results'
os.makedirs(RESULTS_DIR, exist_ok=True)

# 日志配置
LOG_FILE = os.path.join(RESULTS_DIR, f'performance_test_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')


def log(message):
    """记录日志"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f'[{timestamp}] {message}'
    print(log_entry)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_entry + '\n')


def test_api_response_time(endpoint, params=None):
    """测试API响应时间"""
    url = f'{BASE_URL}{endpoint}'
    start_time = time.time()
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        end_time = time.time()
        response_time = end_time - start_time
        return {
            'success': True,
            'status_code': response.status_code,
            'response_time': response_time,
            'url': url
        }
    except Exception as e:
        end_time = time.time()
        return {
            'success': False,
            'status_code': None,
            'response_time': end_time - start_time,
            'error': str(e),
            'url': url
        }


def test_paginated_response(endpoint, page, per_page):
    """测试分页响应"""
    # 资材换算页面不支持分页参数
    if '/material_calculator' in endpoint:
        return test_api_response_time(endpoint)
    
    params = {
        'page': page,
        'per_page': per_page
    }
    return test_api_response_time(endpoint, params=params)


def run_performance_tests():
    """运行性能测试"""
    log("开始性能测试...")
    log(f"测试基础URL: {BASE_URL}")
    log(f"测试端点: {', '.join(TEST_ENDPOINTS)}")
    log(f"测试数据量级: {', '.join(map(str, TEST_DATA_SIZES))}")
    log(f"每个测试迭代次数: {TEST_ITERATIONS}")
    log("=" * 60)

    # 测试结果
    results = []

    for endpoint in TEST_ENDPOINTS:
        log(f"\n测试端点: {endpoint}")
        log("-" * 40)

        for per_page in TEST_DATA_SIZES:
            log(f"\n测试每页显示 {per_page} 条记录")
            
            # 测试多次取平均值
            total_response_time = 0
            successful_tests = 0
            
            for i in range(TEST_ITERATIONS):
                log(f"  迭代 {i+1}/{TEST_ITERATIONS}")
                result = test_paginated_response(endpoint, 1, per_page)
                
                if result['success']:
                    total_response_time += result['response_time']
                    successful_tests += 1
                    log(f"    ✅ 成功 - 响应时间: {result['response_time']:.3f} 秒")
                else:
                    log(f"    ❌ 失败 - 错误: {result['error']}")

            # 计算平均响应时间
            if successful_tests > 0:
                avg_response_time = total_response_time / successful_tests
                log(f"  平均响应时间: {avg_response_time:.3f} 秒")
                log(f"  成功测试次数: {successful_tests}/{TEST_ITERATIONS}")
                
                # 保存结果
                results.append({
                    'endpoint': endpoint,
                    'data_size': per_page,
                    'avg_response_time': avg_response_time,
                    'successful_tests': successful_tests,
                    'total_tests': TEST_ITERATIONS,
                    'timestamp': datetime.now().isoformat()
                })
            else:
                log(f"  ❌ 所有测试均失败")

    # 保存测试结果到CSV文件
    csv_file = os.path.join(RESULTS_DIR, f'performance_results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['endpoint', 'data_size', 'avg_response_time', 'successful_tests', 'total_tests', 'timestamp']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(result)

    log(f"\n{'-' * 60}")
    log(f"性能测试完成！")
    log(f"测试结果已保存到: {csv_file}")
    log(f"测试日志已保存到: {LOG_FILE}")

    # 生成测试报告
    generate_test_report(results, csv_file)


def generate_test_report(results, csv_file):
    """生成测试报告"""
    report_file = csv_file.replace('.csv', '.md')
    
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write('# 性能测试报告\n\n')
        f.write(f'**测试时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n\n')
        f.write(f'**测试URL**: {BASE_URL}\n\n')
        f.write('## 测试结果汇总\n\n')
        f.write('| 测试端点 | 数据量级 | 平均响应时间 (秒) | 成功测试次数 | 总测试次数 |\n')
        f.write('|---------|---------|----------------|------------|----------|\n')
        
        for result in results:
            f.write(f"| {result['endpoint']} | {result['data_size']} | {result['avg_response_time']:.3f} | {result['successful_tests']} | {result['total_tests']} |\n")
        
        f.write('\n## 测试结论\n\n')
        
        # 分析测试结果
        all_passed = True
        for result in results:
            if result['avg_response_time'] > 2.0:
                f.write(f"❌ **{result['endpoint']}** 在数据量级 {result['data_size']} 下的平均响应时间为 {result['avg_response_time']:.3f} 秒，超过了2秒的目标。\n")
                all_passed = False
            else:
                f.write(f"✅ **{result['endpoint']}** 在数据量级 {result['data_size']} 下的平均响应时间为 {result['avg_response_time']:.3f} 秒，符合2秒以内的目标。\n")
        
        f.write('\n## 优化建议\n\n')
        if all_passed:
            f.write('所有测试均已通过！当前系统性能良好，可以考虑进一步优化数据库索引或前端渲染逻辑，以获得更好的性能。\n')
        else:
            f.write('1. 考虑优化数据库查询，添加更多复合索引\n')
            f.write('2. 实现前端数据缓存，减少重复请求\n')
            f.write('3. 优化前端渲染逻辑，减少DOM操作\n')
            f.write('4. 考虑使用虚拟滚动，只渲染可视区域内的数据\n')
            f.write('5. 优化数据传输，使用更高效的数据格式\n')
    
    log(f"测试报告已生成: {report_file}")


if __name__ == '__main__':
    run_performance_tests()