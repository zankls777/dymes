// 前端数据缓存工具类
class DataCache {
    constructor(cacheName, defaultTTL = 3600000) {
        this.cacheName = cacheName;
        this.defaultTTL = defaultTTL; // 默认缓存1小时
        this.cache = this._loadCache();
    }

    // 从localStorage加载缓存
    _loadCache() {
        try {
            const cachedData = localStorage.getItem(this.cacheName);
            if (cachedData) {
                const parsed = JSON.parse(cachedData);
                // 清理过期缓存
                this._cleanupExpired();
                return parsed;
            }
        } catch (error) {
            console.error('加载缓存失败:', error);
        }
        return {};
    }

    // 保存缓存到localStorage
    _saveCache() {
        try {
            localStorage.setItem(this.cacheName, JSON.stringify(this.cache));
        } catch (error) {
            console.error('保存缓存失败:', error);
        }
    }

    // 清理过期缓存
    _cleanupExpired() {
        const now = Date.now();
        for (const key in this.cache) {
            if (this.cache[key].expireAt < now) {
                delete this.cache[key];
            }
        }
        this._saveCache();
    }

    // 生成缓存键
    _generateKey(key, params = {}) {
        if (typeof params === 'object' && params !== null) {
            // 将参数排序后拼接，确保相同参数生成相同的键
            const sortedParams = Object.keys(params).sort().map(k => `${k}=${params[k]}`).join('&');
            return `${key}?${sortedParams}`;
        }
        return key;
    }

    // 设置缓存
    set(key, data, params = {}, ttl = null) {
        const cacheKey = this._generateKey(key, params);
        const expireAt = Date.now() + (ttl || this.defaultTTL);
        this.cache[cacheKey] = {
            data: data,
            expireAt: expireAt,
            lastUpdated: Date.now()
        };
        this._saveCache();
    }

    // 获取缓存
    get(key, params = {}) {
        const cacheKey = this._generateKey(key, params);
        const cachedItem = this.cache[cacheKey];
        if (cachedItem) {
            if (cachedItem.expireAt > Date.now()) {
                return cachedItem.data;
            } else {
                // 缓存过期，删除
                delete this.cache[cacheKey];
                this._saveCache();
            }
        }
        return null;
    }

    // 检查缓存是否存在且有效
    has(key, params = {}) {
        const cacheKey = this._generateKey(key, params);
        const cachedItem = this.cache[cacheKey];
        return cachedItem && cachedItem.expireAt > Date.now();
    }

    // 删除缓存
    remove(key, params = {}) {
        const cacheKey = this._generateKey(key, params);
        if (this.cache[cacheKey]) {
            delete this.cache[cacheKey];
            this._saveCache();
        }
    }

    // 清除所有缓存
    clear() {
        this.cache = {};
        this._saveCache();
    }

    // 更新缓存过期时间
    touch(key, params = {}, ttl = null) {
        const cacheKey = this._generateKey(key, params);
        const cachedItem = this.cache[cacheKey];
        if (cachedItem) {
            cachedItem.expireAt = Date.now() + (ttl || this.defaultTTL);
            this._saveCache();
        }
    }

    // 获取缓存信息
    getInfo(key, params = {}) {
        const cacheKey = this._generateKey(key, params);
        return this.cache[cacheKey] || null;
    }

    // 获取所有缓存键
    getAllKeys() {
        return Object.keys(this.cache);
    }
}

// 创建全局缓存实例
window.appCache = {
    subList: new DataCache('subListCache', 3600000),
    usageList: new DataCache('usageListCache', 3600000),
    fileManagement: new DataCache('fileManagementCache', 3600000),
    columnSelection: new DataCache('columnSelectionCache', 86400000) // 列选择状态缓存1天
};

// 工具函数：防抖
function debounce(func, wait, immediate = false) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            timeout = null;
            if (!immediate) func(...args);
        };
        const callNow = immediate && !timeout;
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
        if (callNow) func(...args);
    };
}

// 工具函数：节流
function throttle(func, limit) {
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

// 工具函数：格式化日期
function formatDate(date) {
    if (!date) return '';
    const d = new Date(date);
    return d.toLocaleString();
}

// 工具函数：生成唯一ID
function generateId() {
    return Date.now().toString(36) + Math.random().toString(36).substr(2);
}