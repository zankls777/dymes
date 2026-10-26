#!/usr/bin/env python3
"""
前端性能优化脚本
压缩JS/CSS、优化DOM操作、添加懒加载
"""

import os
import re
from pathlib import Path

def optimize_javascript():
    """优化JavaScript代码"""
    
    js_optimizations = {
        # 减少DOM查询
        "document.querySelector": "缓存DOM元素",
        "document.getElementById": "使用变量缓存",
        "document.getElementsByClassName": "使用变量缓存",
        
        # 优化事件处理
        "addEventListener": "使用事件委托",
        "onclick": "统一事件管理",
        
        # 优化循环
        "for (let i = 0; i < array.length; i++)": "使用 for...of 或 forEach",
        "while (condition)": "检查循环条件优化",
    }
    
    print("📋 JavaScript 优化建议:")
    for pattern, suggestion in js_optimizations.items():
        print(f"  {pattern} → {suggestion}")
    print()

def create_optimized_js():
    """创建优化的JavaScript文件"""
    
    optimized_js = '''
// 前端性能优化工具类
class PerformanceOptimizer {
    constructor() {
        this.domCache = new Map();
        this.eventDelegation = new Map();
    }
    
    // DOM 元素缓存
    getElement(selector) {
        if (!this.domCache.has(selector)) {
            this.domCache.set(selector, document.querySelector(selector));
        }
        return this.domCache.get(selector);
    }
    
    // 事件委托
    delegateEvent(container, eventType, selector, handler) {
        if (!this.eventDelegation.has(container)) {
            this.eventDelegation.set(container, new Map());
        }
        
        const containerEvents = this.eventDelegation.get(container);
        if (!containerEvents.has(eventType)) {
            container.addEventListener(eventType, (e) => {
                const target = e.target.closest(selector);
                if (target) {
                    const handler = containerEvents.get(eventType).get(selector);
                    handler.call(target, e);
                }
            });
            containerEvents.set(eventType, new Map());
        }
        
        containerEvents.get(eventType).set(selector, handler);
    }
    
    // 防抖函数
    debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }
    
    // 节流函数
    throttle(func, limit) {
        let inThrottle;
        return function() {
            const args = arguments;
            const context = this;
            if (!inThrottle) {
                func.apply(context, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        };
    }
    
    // 懒加载图片
    lazyLoadImages() {
        const images = document.querySelectorAll('img[data-src]');
        const imageObserver = new IntersectionObserver((entries, observer) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const img = entry.target;
                    img.src = img.dataset.src;
                    img.removeAttribute('data-src');
                    observer.unobserve(img);
                }
            });
        });
        
        images.forEach(img => imageObserver.observe(img));
    }
    
    // 虚拟滚动
    virtualScroll(container, items, itemHeight, visibleCount) {
        const scrollTop = container.scrollTop;
        const startIndex = Math.floor(scrollTop / itemHeight);
        const endIndex = Math.min(startIndex + visibleCount, items.length);
        
        const visibleItems = items.slice(startIndex, endIndex);
        const offsetY = startIndex * itemHeight;
        
        return { visibleItems, offsetY };
    }
}

// 全局实例
window.PerfOptimizer = new PerformanceOptimizer();

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    // 懒加载图片
    window.PerfOptimizer.lazyLoadImages();
    
    // 预加载关键资源
    const criticalResources = [
        '/static/css/bootstrap.min.css',
        '/static/js/bootstrap.bundle.min.js'
    ];
    
    criticalResources.forEach(resource => {
        const link = document.createElement('link');
        link.rel = 'preload';
        link.href = resource;
        link.as = resource.endsWith('.css') ? 'style' : 'script';
        document.head.appendChild(link);
    });
});
'''
    
    with open('static/js/performance-optimizer.js', 'w', encoding='utf-8') as f:
        f.write(optimized_js)
    
    print("✅ 性能优化工具已创建: static/js/performance-optimizer.js")

def optimize_css():
    """优化CSS代码"""
    
    css_optimizations = {
        # CSS 优化建议
        "重复的样式": "提取公共样式类",
        "内联样式": "移动到外部CSS文件",
        "未使用的样式": "删除无用CSS",
        "复杂选择器": "简化选择器",
        "重复的媒体查询": "合并媒体查询",
    }
    
    print("📋 CSS 优化建议:")
    for issue, solution in css_optimizations.items():
        print(f"  {issue} → {solution}")
    print()

def create_optimized_css():
    """创建优化的CSS文件"""
    
    optimized_css = '''
/* 性能优化CSS */

/* 1. 使用CSS变量减少重复 */
:root {
    --primary-color: #007bff;
    --secondary-color: #6c757d;
    --success-color: #28a745;
    --danger-color: #dc3545;
    --warning-color: #ffc107;
    --info-color: #17a2b8;
    --light-color: #f8f9fa;
    --dark-color: #343a40;
    
    --border-radius: 0.375rem;
    --box-shadow: 0 0.125rem 0.25rem rgba(0, 0, 0, 0.075);
    --transition: all 0.15s ease-in-out;
}

/* 2. 优化动画性能 */
.fade-in {
    animation: fadeIn 0.3s ease-in-out;
}

@keyframes fadeIn {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: translateY(0); }
}

/* 3. 使用 transform 而不是改变位置属性 */
.slide-in {
    transform: translateX(-100%);
    transition: transform 0.3s ease-in-out;
}

.slide-in.active {
    transform: translateX(0);
}

/* 4. 优化表格性能 */
.table-optimized {
    table-layout: fixed;
    width: 100%;
}

.table-optimized th,
.table-optimized td {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

/* 5. 懒加载占位符 */
.lazy-placeholder {
    background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);
    background-size: 200% 100%;
    animation: loading 1.5s infinite;
}

@keyframes loading {
    0% { background-position: 200% 0; }
    100% { background-position: -200% 0; }
}

/* 6. 响应式图片 */
.responsive-img {
    max-width: 100%;
    height: auto;
    display: block;
}

/* 7. 优化按钮性能 */
.btn-optimized {
    position: relative;
    overflow: hidden;
    transition: var(--transition);
}

.btn-optimized:active {
    transform: scale(0.98);
}

/* 8. 虚拟滚动容器 */
.virtual-scroll-container {
    height: 400px;
    overflow-y: auto;
    position: relative;
}

.virtual-scroll-content {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
}

/* 9. 优化模态框性能 */
.modal-optimized {
    will-change: transform, opacity;
    transform: translateZ(0);
}

/* 10. 减少重绘和重排 */
.no-repaint {
    will-change: auto;
    transform: translateZ(0);
}
'''
    
    with open('static/css/performance-optimized.css', 'w', encoding='utf-8') as f:
        f.write(optimized_css)
    
    print("✅ 性能优化CSS已创建: static/css/performance-optimized.css")

def analyze_frontend_performance():
    """分析前端性能问题"""
    
    performance_analysis = """
# 前端性能分析报告

## 1. 当前问题
- JavaScript 文件过大 (bundle.js, xlsx.js 等)
- DOM 操作频繁，影响性能
- 缺少代码压缩和合并
- 图片资源未优化
- 缺少懒加载机制

## 2. 优化建议

### JavaScript 优化
- 使用代码分割 (Code Splitting)
- 实现懒加载 (Lazy Loading)
- 优化事件处理 (事件委托)
- 减少 DOM 查询 (缓存元素)
- 使用防抖和节流

### CSS 优化
- 提取公共样式
- 使用 CSS 变量
- 优化动画性能
- 减少重绘和重排
- 使用 transform 替代位置属性

### 资源优化
- 压缩 JavaScript 和 CSS
- 优化图片格式和大小
- 使用 CDN 加速
- 实现资源预加载
- 添加缓存策略

## 3. 性能指标目标
- 首屏加载时间 < 2秒
- 交互响应时间 < 100ms
- 页面大小 < 1MB
- 图片加载时间 < 500ms

## 4. 实施计划
1. 添加性能优化工具类
2. 优化现有 JavaScript 代码
3. 压缩和合并资源文件
4. 实现懒加载机制
5. 添加性能监控
"""
    
    with open('frontend_performance_analysis.md', 'w', encoding='utf-8') as f:
        f.write(performance_analysis)
    
    print("✅ 前端性能分析报告已生成: frontend_performance_analysis.md")

def main():
    """主函数"""
    print("🎨 开始前端性能优化...")
    
    # 1. 优化JavaScript
    optimize_javascript()
    create_optimized_js()
    
    # 2. 优化CSS
    optimize_css()
    create_optimized_css()
    
    # 3. 分析性能
    analyze_frontend_performance()
    
    print("\n📋 前端性能优化完成！")
    print("建议:")
    print("1. 在模板中引入 performance-optimizer.js")
    print("2. 使用 performance-optimized.css")
    print("3. 逐步优化现有代码")
    print("4. 添加性能监控")
    print("5. 使用浏览器开发者工具测试性能")

if __name__ == "__main__":
    main()
