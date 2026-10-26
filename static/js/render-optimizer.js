// 前端渲染优化工具
class RenderOptimizer {
    constructor() {
        this.batchedUpdates = []; // 批量更新队列
        this.updateScheduled = false; // 是否已安排更新
        this.domCache = new Map(); // DOM元素缓存
        this.templateCache = new Map(); // 模板缓存
        this.observers = new Map(); // 数据观察者
    }
    
    // 批量更新DOM
    batchUpdate(updateFn) {
        this.batchedUpdates.push(updateFn);
        if (!this.updateScheduled) {
            this.updateScheduled = true;
            // 使用requestAnimationFrame确保在浏览器下一次重绘前执行
            requestAnimationFrame(() => {
                this.flushUpdates();
            });
        }
    }
    
    // 执行批量更新
    flushUpdates() {
        try {
            // 执行所有更新函数
            this.batchedUpdates.forEach(updateFn => {
                updateFn();
            });
        } catch (error) {
            console.error('批量更新执行失败:', error);
        } finally {
            // 清空队列并重置标记
            this.batchedUpdates = [];
            this.updateScheduled = false;
        }
    }
    
    // 创建模板函数，缓存编译结果
    createTemplate(templateStr) {
        // 检查缓存
        if (this.templateCache.has(templateStr)) {
            return this.templateCache.get(templateStr);
        }
        
        // 编译模板
        const templateFn = (data) => {
            let result = templateStr;
            // 简单的模板替换，支持 {{key}} 语法
            Object.keys(data).forEach(key => {
                const regex = new RegExp(`{{${key}}}`, 'g');
                result = result.replace(regex, data[key] || '');
            });
            return result;
        };
        
        // 缓存编译结果
        this.templateCache.set(templateStr, templateFn);
        return templateFn;
    }
    
    // 渲染模板
    renderTemplate(templateStr, data) {
        const templateFn = this.createTemplate(templateStr);
        return templateFn(data);
    }
    
    // 批量渲染列表
    renderList(container, items, templateStr, keyField = 'id') {
        if (!container) return;
        
        // 创建文档片段，减少DOM操作次数
        const fragment = document.createDocumentFragment();
        
        // 渲染所有项
        items.forEach(item => {
            const html = this.renderTemplate(templateStr, item);
            const div = document.createElement('div');
            div.innerHTML = html;
            const element = div.firstElementChild;
            if (element) {
                // 设置唯一标识
                if (keyField && item[keyField]) {
                    element.dataset.key = item[keyField];
                }
                fragment.appendChild(element);
            }
        });
        
        // 使用批量更新
        this.batchUpdate(() => {
            // 清空容器并添加新内容
            container.innerHTML = '';
            container.appendChild(fragment);
        });
    }
    
    // 差量更新列表（仅更新变化的项）
    diffUpdateList(container, newItems, templateStr, keyField = 'id') {
        if (!container) return;
        
        this.batchUpdate(() => {
            // 获取现有项
            const existingItems = new Map();
            container.querySelectorAll(`[data-key]`).forEach(element => {
                existingItems.set(element.dataset.key, element);
            });
            
            // 创建文档片段
            const fragment = document.createDocumentFragment();
            
            // 需要保留的项的键集合
            const keysToKeep = new Set();
            
            // 遍历新项
            newItems.forEach(item => {
                const key = item[keyField];
                keysToKeep.add(key);
                
                // 如果项已存在，更新它
                if (existingItems.has(key)) {
                    const element = existingItems.get(key);
                    const html = this.renderTemplate(templateStr, item);
                    const div = document.createElement('div');
                    div.innerHTML = html;
                    const newElement = div.firstElementChild;
                    if (newElement) {
                        // 替换现有元素
                        element.replaceWith(newElement);
                    }
                } else {
                    // 创建新元素
                    const html = this.renderTemplate(templateStr, item);
                    const div = document.createElement('div');
                    div.innerHTML = html;
                    const newElement = div.firstElementChild;
                    if (newElement) {
                        newElement.dataset.key = key;
                        fragment.appendChild(newElement);
                    }
                }
            });
            
            // 删除不再需要的项
            container.querySelectorAll(`[data-key]`).forEach(element => {
                if (!keysToKeep.has(element.dataset.key)) {
                    element.remove();
                }
            });
            
            // 添加新创建的项
            if (fragment.children.length > 0) {
                container.appendChild(fragment);
            }
        });
    }
    
    // 获取或创建DOM元素（缓存机制）
    getElement(selector, parent = document) {
        // 检查缓存
        if (this.domCache.has(selector)) {
            return this.domCache.get(selector);
        }
        
        // 查找元素
        const element = parent.querySelector(selector);
        if (element) {
            // 缓存元素
            this.domCache.set(selector, element);
        }
        
        return element;
    }
    
    // 清空DOM缓存
    clearDomCache() {
        this.domCache.clear();
    }
    
    // 清空模板缓存
    clearTemplateCache() {
        this.templateCache.clear();
    }
    
    // 清空所有缓存
    clearCache() {
        this.clearDomCache();
        this.clearTemplateCache();
    }
    
    // 防抖渲染
    debounceRender(renderFn, wait = 100) {
        let timeout;
        return function(...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => {
                renderFn.apply(this, args);
            }, wait);
        };
    }
    
    // 节流渲染
    throttleRender(renderFn, limit = 50) {
        let inThrottle;
        return function(...args) {
            if (!inThrottle) {
                renderFn.apply(this, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        };
    }
    
    // 懒加载组件
    lazyLoadComponent(selector, loadFn) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    // 元素进入视口，加载组件
                    loadFn(entry.target);
                    // 停止观察
                    observer.unobserve(entry.target);
                }
            });
        });
        
        // 观察所有匹配的元素
        document.querySelectorAll(selector).forEach(element => {
            observer.observe(element);
        });
        
        return observer;
    }
    
    // 优化表格渲染
    optimizeTable(table, options = {}) {
        if (!table) return;
        
        const { 
            virtualScroll = true, 
            fixedHeader = true,
            itemHeight = 40
        } = options;
        
        // 固定表头
        if (fixedHeader) {
            const thead = table.querySelector('thead');
            if (thead) {
                thead.style.position = 'sticky';
                thead.style.top = '0';
                thead.style.zIndex = '10';
                thead.style.backgroundColor = '#fff';
            }
        }
        
        // 虚拟滚动
        if (virtualScroll) {
            // 检查是否已加载虚拟滚动组件
            if (window.TableVirtualScroll) {
                const tbody = table.querySelector('tbody');
                if (tbody) {
                    // 这里可以初始化虚拟滚动
                    // 实际使用时需要根据具体情况配置
                    console.log('表格虚拟滚动已启用');
                }
            }
        }
        
        // 优化表格行渲染
        const rows = table.querySelectorAll('tbody tr');
        rows.forEach(row => {
            // 添加适当的样式，优化渲染
            row.style.contain = 'layout size';
        });
    }
    
    // 监听数据变化，自动更新UI
    observeData(data, callback) {
        // 创建Proxy对象，监听数据变化
        const proxy = new Proxy(data, {
            set(target, property, value) {
                const oldValue = target[property];
                target[property] = value;
                // 数据变化时调用回调
                callback(property, oldValue, value);
                return true;
            },
            deleteProperty(target, property) {
                const oldValue = target[property];
                delete target[property];
                // 数据变化时调用回调
                callback(property, oldValue, undefined);
                return true;
            }
        });
        
        return proxy;
    }
}

// 创建全局渲染优化实例
window.renderOptimizer = new RenderOptimizer();

// DOM操作工具
class DOMUtils {
    // 创建元素
    static createElement(tagName, attributes = {}, children = []) {
        const element = document.createElement(tagName);
        
        // 设置属性
        Object.keys(attributes).forEach(key => {
            if (key === 'className') {
                element.className = attributes[key];
            } else if (key === 'innerHTML') {
                element.innerHTML = attributes[key];
            } else if (key === 'textContent') {
                element.textContent = attributes[key];
            } else if (key.startsWith('data-')) {
                element.setAttribute(key, attributes[key]);
            } else {
                element[key] = attributes[key];
            }
        });
        
        // 添加子元素
        children.forEach(child => {
            if (typeof child === 'string') {
                element.appendChild(document.createTextNode(child));
            } else if (child instanceof Node) {
                element.appendChild(child);
            }
        });
        
        return element;
    }
    
    // 批量添加元素
    static appendChildren(parent, children) {
        const fragment = document.createDocumentFragment();
        children.forEach(child => {
            if (typeof child === 'string') {
                fragment.appendChild(document.createTextNode(child));
            } else if (child instanceof Node) {
                fragment.appendChild(child);
            }
        });
        parent.appendChild(fragment);
    }
    
    // 移除所有子元素
    static clearChildren(element) {
        while (element.firstChild) {
            element.removeChild(element.firstChild);
        }
    }
    
    // 获取元素位置信息
    static getElementPosition(element) {
        return element.getBoundingClientRect();
    }
    
    // 检查元素是否在视口内
    static isInViewport(element, margin = 0) {
        const rect = element.getBoundingClientRect();
        return (
            rect.top <= (window.innerHeight + margin) &&
            rect.bottom >= -margin &&
            rect.left <= (window.innerWidth + margin) &&
            rect.right >= -margin
        );
    }
    
    // 添加事件监听器，支持事件委托
    static addEventListener(element, event, selector, handler) {
        if (handler) {
            // 事件委托
            element.addEventListener(event, (e) => {
                const target = e.target.closest(selector);
                if (target) {
                    handler.call(target, e, target);
                }
            });
        } else {
            // 直接监听
            element.addEventListener(event, selector);
        }
    }
}

// 导出工具
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { RenderOptimizer, DOMUtils };
}