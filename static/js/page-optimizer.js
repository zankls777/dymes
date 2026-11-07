/**
 * 高性能数据加载和渲染管理器
 * 为 sub_list 和 usage_list 页面提供优化支持
 */

// 数据加载管理器
class DataLoaderManager {
    constructor(options = {}) {
        this.options = {
            pageSize: options.pageSize || 10,
            maxRetries: options.maxRetries || 3,
            cacheTTL: options.cacheTTL || 5 * 60 * 1000, // 5分钟
            ...options
        };
        
        this._cache = new Map();
        this._pendingRequests = new Map();
        this._loadTimers = new Map();
        this._stats = {
            requests: 0,
            cacheHits: 0,
            cacheMisses: 0,
            averageLoadTime: 0
        };
    }
    
    /**
     * 生成缓存键
     */
    generateCacheKey(endpoint, params = {}) {
        const sortedParams = Object.keys(params)
            .filter(k => params[k] !== '' && params[k] !== null && params[k] !== undefined)
            .sort()
            .map(k => `${k}=${encodeURIComponent(params[k])}`)
            .join('&');
        return `${endpoint}?${sortedParams}`;
    }
    
    /**
     * 获取缓存
     */
    getCache(key) {
        const cached = this._cache.get(key);
        if (cached && Date.now() - cached.timestamp < this.options.cacheTTL) {
            this._stats.cacheHits++;
            return cached.data;
        }
        if (cached) {
            this._cache.delete(key);
        }
        this._stats.cacheMisses++;
        return null;
    }
    
    /**
     * 设置缓存
     */
    setCache(key, data) {
        this._cache.set(key, {
            data: data,
            timestamp: Date.now()
        });
        
        // 限制缓存大小
        if (this._cache.size > 500) {
            const oldestKey = this._cache.keys().next().value;
            this._cache.delete(oldestKey);
        }
    }
    
    /**
     * 清除缓存
     */
    clearCache(endpoint = null) {
        if (endpoint) {
            const prefix = endpoint + '?';
            for (const key of this._cache.keys()) {
                if (key.startsWith(prefix)) {
                    this._cache.delete(key);
                }
            }
        } else {
            this._cache.clear();
        }
    }
    
    /**
     * 加载数据（带缓存和重试）
     */
    async loadData(endpoint, params = {}, options = {}) {
        const {
            forceRefresh = false,
            onProgress = null,
            signal = null
        } = options;
        
        const cacheKey = this.generateCacheKey(endpoint, params);
        
        // 检查缓存
        if (!forceRefresh) {
            const cached = this.getCache(cacheKey);
            if (cached) {
                return cached;
            }
        }
        
        // 检查是否有相同的请求正在进行
        if (this._pendingRequests.has(cacheKey)) {
            return this._pendingRequests.get(cacheKey);
        }
        
        // 创建新请求
        const requestPromise = this._fetchWithRetry(endpoint, params, onProgress, signal)
            .then(data => {
                this.setCache(cacheKey, data);
                this._pendingRequests.delete(cacheKey);
                return data;
            })
            .catch(error => {
                this._pendingRequests.delete(cacheKey);
                throw error;
            });
        
        this._pendingRequests.set(cacheKey, requestPromise);
        return requestPromise;
    }
    
    /**
     * 带重试的请求
     */
    async _fetchWithRetry(endpoint, params, onProgress, signal, retryCount = 0) {
        const startTime = performance.now();
        this._stats.requests++;
        
        try {
            // 关键修复：将搜索参数拼接到 URL 查询串
            // （此前参数被忽略，导致实时搜索永远返回未过滤的第一页数据）
            const query = new URLSearchParams();
            Object.entries(params || {}).forEach(([key, val]) => {
                if (val !== '' && val !== null && val !== undefined) {
                    query.set(key, val);
                }
            });
            const queryString = query.toString();
            const url = queryString ? `${endpoint}?${queryString}` : endpoint;

            const response = await fetch(url, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                },
                signal: signal
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            const loadTime = performance.now() - startTime;
            
            // 更新平均加载时间
            this._stats.averageLoadTime = 
                (this._stats.averageLoadTime * 0.9 + loadTime * 0.1);
            
            if (onProgress) {
                onProgress({
                    progress: 100,
                    loadTime: loadTime,
                    itemCount: data.items?.length || 0
                });
            }
            
            return data;
        } catch (error) {
            if (error.name === 'AbortError') {
                throw error;
            }
            
            if (retryCount < this.options.maxRetries) {
                // 指数退避重试
                const delay = Math.pow(2, retryCount) * 100;
                await new Promise(resolve => setTimeout(resolve, delay));
                return this._fetchWithRetry(
                    endpoint, params, onProgress, signal, retryCount + 1
                );
            }
            
            throw error;
        }
    }
    
    /**
     * 预加载数据
     */
    preload(endpoint, params = {}) {
        const cacheKey = this.generateCacheKey(endpoint, params);
        if (!this._cache.has(cacheKey)) {
            this.loadData(endpoint, params).catch(() => {});
        }
    }
    
    /**
     * 预加载多个页面
     */
    preloadPages(endpoint, baseParams, pages = [1, 2, 3]) {
        pages.forEach(page => {
            this.preload(endpoint, { ...baseParams, page });
        });
    }
    
    /**
     * 获取统计信息
     */
    getStats() {
        return {
            ...this._stats,
            cacheSize: this._cache.size,
            pendingRequests: this._pendingRequests.size
        };
    }
}


// 表格渲染优化器
class TableRenderer {
    constructor(tableElement, options = {}) {
        this.table = tableElement;
        this.tbody = tableElement.querySelector('tbody');
        this.options = {
            virtualScroll: options.virtualScroll !== false,
            batchSize: options.batchSize || 50,
            itemHeight: options.itemHeight || 40,
            ...options
        };
        
        this._data = [];
        this._renderedRange = { start: 0, end: 0 };
        this._scrollContainer = null;
        this._scrollHandler = null;
    }
    
    /**
     * 设置数据并渲染
     */
    setData(data) {
        this._data = data || [];
        this.render();
    }
    
    /**
     * 渲染表格
     */
    render() {
        if (!this._data.length) {
            this._showEmptyState();
            return;
        }
        
        if (this.options.virtualScroll && this._data.length > this.options.batchSize) {
            this._setupVirtualScroll();
            this._renderVisibleRange();
        } else {
            this._renderAll();
        }
    }
    
    /**
     * 渲染所有数据（小数据集）
     */
    _renderAll() {
        const fragment = document.createDocumentFragment();
        
        this._data.forEach(item => {
            fragment.appendChild(this._createRow(item));
        });
        
        this.tbody.innerHTML = '';
        this.tbody.appendChild(fragment);
    }
    
    /**
     * 配置虚拟滚动
     */
    _setupVirtualScroll() {
        // 获取或创建滚动容器
        this._scrollContainer = this.table.closest('.table-container');
        if (!this._scrollContainer) {
            this._scrollContainer = this.table.parentElement;
        }
        
        // 设置样式
        this._scrollContainer.style.maxHeight = '600px';
        this._scrollContainer.style.overflow = 'auto';
        this._scrollContainer.style.position = 'relative';
        
        // 设置tbody高度以支持滚动
        this.tbody.style.height = `${this._data.length * this.options.itemHeight}px`;
        this.tbody.style.position = 'relative';
        
        // 绑定滚动事件
        if (this._scrollHandler) {
            this._scrollContainer.removeEventListener('scroll', this._scrollHandler);
        }
        
        this._scrollHandler = this._debounce(() => {
            this._renderVisibleRange();
        }, 16);
        
        this._scrollContainer.addEventListener('scroll', this._scrollHandler);
    }
    
    /**
     * 渲染可视范围
     */
    _renderVisibleRange() {
        if (!this._scrollContainer) return;
        
        const scrollTop = this._scrollContainer.scrollTop;
        const containerHeight = this._scrollContainer.clientHeight;
        
        // 计算可视范围
        const startIndex = Math.max(0, Math.floor(scrollTop / this.options.itemHeight) - 5);
        const endIndex = Math.min(
            this._data.length - 1,
            Math.ceil((scrollTop + containerHeight) / this.options.itemHeight) + 5
        );
        
        // 如果范围没有变化，跳过渲染
        if (startIndex === this._renderedRange.start && 
            endIndex === this._renderedRange.end) {
            return;
        }
        
        this._renderedRange = { start: startIndex, end: endIndex };
        
        // 渲染可视范围内的数据
        const fragment = document.createDocumentFragment();
        
        for (let i = startIndex; i <= endIndex; i++) {
            const row = this._createRow(this._data[i]);
            row.style.position = 'absolute';
            row.style.top = `${i * this.options.itemHeight}px`;
            row.style.left = '0';
            row.style.width = '100%';
            row.style.height = `${this.options.itemHeight}px`;
            row.style.boxSizing = 'border-box';
            fragment.appendChild(row);
        }
        
        this.tbody.innerHTML = '';
        this.tbody.appendChild(fragment);
    }
    
    /**
     * 创建表格行
     */
    _createRow(item) {
        const tr = document.createElement('tr');
        
        // 根据表格的th数量创建单元格
        const headers = this.table.querySelectorAll('thead th');
        headers.forEach((header, index) => {
            const td = document.createElement('td');
            const fieldName = this._getFieldName(header, index);
            td.textContent = item[fieldName] || '';
            td.className = header.className;
            tr.appendChild(td);
        });
        
        return tr;
    }
    
    /**
     * 获取字段名
     */
    _getFieldName(header, index) {
        // 尝试从data属性获取，或使用索引映射
        if (header.dataset.field) {
            return header.dataset.field;
        }
        
        // 默认映射（sub_list字段）
        const subListFields = [
            'VehicleType', 'SubPno', 'EoKsc', 'SetCode', 'Circuit', 
            'Wire', 'WireCode', 'Color', 'SQ', 'Length_Cut', 
            'Length_Cut2', 'Tml1', 'Tml2', 'Rel1', 'Rel2', 
            'Seal1', 'Seal2', 'Joint_Points', 'Remark1'
        ];
        
        // 默认映射（usage_list字段）
        const usageListFields = [
            'VehicleType', 'SubPno', 'EoKsc', 'Comp_Kind', 'Detail_Kind',
            'KscPno', 'Part_Name', 'Usage', 'Length'
        ];
        
        // 检测表格类型
        if (this.table.id === 'data-table') {
            const fieldCount = this.table.querySelectorAll('thead th').length;
            if (fieldCount > 10) {
                return subListFields[index] || `field_${index}`;
            } else {
                return usageListFields[index] || `field_${index}`;
            }
        }
        
        return `field_${index}`;
    }
    
    /**
     * 显示空状态
     */
    _showEmptyState() {
        this.tbody.innerHTML = `
            <tr>
                <td colspan="${this.table.querySelectorAll('thead th').length}" class="text-center py-5 text-muted">
                    <i class="fa fa-search fa-4x mb-3"></i>
                    <p class="mb-0">没有找到匹配的数据</p>
                    <p class="text-sm">请尝试调整搜索条件</p>
                </td>
            </tr>
        `;
    }
    
    /**
     * 防抖函数
     */
    _debounce(func, wait) {
        let timeout;
        return function(...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    }
    
    /**
     * 销毁
     */
    destroy() {
        if (this._scrollContainer && this._scrollHandler) {
            this._scrollContainer.removeEventListener('scroll', this._scrollHandler);
        }
    }
}


// 页面加载状态管理器
class PageLoadingManager {
    constructor() {
        this._loadingCount = 0;
        this._listeners = new Set();
    }
    
    /**
     * 显示加载状态
     */
    showLoading(containerId = 'main-content') {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        this._loadingCount++;
        container.dataset.loading = 'true';
        
        // 创建加载指示器
        if (!container.querySelector('.page-loading-indicator')) {
            const indicator = document.createElement('div');
            indicator.className = 'page-loading-indicator';
            indicator.innerHTML = `
                <div class="spinner-overlay">
                    <div class="spinner-border text-primary" role="status">
                        <span class="visually-hidden">加载中...</span>
                    </div>
                </div>
            `;
            container.appendChild(indicator);
            
            // 添加加载状态样式
            if (!document.getElementById('loading-styles')) {
                const style = document.createElement('style');
                style.id = 'loading-styles';
                style.textContent = `
                    .page-loading-indicator {
                        position: absolute;
                        top: 0;
                        left: 0;
                        right: 0;
                        bottom: 0;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        background: rgba(255, 255, 255, 0.8);
                        z-index: 1000;
                    }
                    .spinner-overlay {
                        text-align: center;
                    }
                    .spinner-border {
                        width: 3rem;
                        height: 3rem;
                        border-width: 0.25em;
                    }
                    [data-loading="true"] {
                        position: relative;
                    }
                `;
                document.head.appendChild(style);
            }
        }
    }
    
    /**
     * 隐藏加载状态
     */
    hideLoading(containerId = 'main-content') {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        this._loadingCount = Math.max(0, this._loadingCount - 1);
        
        if (this._loadingCount === 0) {
            container.dataset.loading = 'false';
            const indicator = container.querySelector('.page-loading-indicator');
            if (indicator) {
                indicator.remove();
            }
        }
    }
    
    /**
     * 监听状态变化
     */
    onChange(listener) {
        this._listeners.add(listener);
        return () => this._listeners.delete(listener);
    }
    
    get isLoading() {
        return this._loadingCount > 0;
    }
}


// 初始化全局管理器
window.dataLoader = new DataLoaderManager({
    pageSize: 10,
    cacheTTL: 5 * 60 * 1000 // 5分钟缓存
});

window.pageLoader = new PageLoadingManager();


/**
 * 表格列字段定义（与模板表头顺序严格一致）
 * 用于 AJAX 局部刷新时直接按字段顺序生成 <td>，无需再次解析 HTML
 */
const SUBLIST_FIELDS = ['VehicleType', 'SubPno', 'EoKsc', 'SetCode', 'Circuit',
    'Wire', 'WireCode', 'Color', 'SQ', 'Length_Cut',
    'Length_Cut2', 'Tml1', 'Tml2', 'Rel1', 'Rel2',
    'Seal1', 'Seal2', 'Joint_Points', 'Remark1'];

const USAGELIST_FIELDS = ['VehicleType', 'SubPno', 'EoKsc', 'Comp_Kind', 'Detail_Kind',
    'KscPno', 'Part_Name', 'Usage', 'Length'];


/**
 * 用 AJAX 返回的数据重建表格 tbody（局部刷新，避免整页重载）
 * @param {Array<Object>} items  数据行
 * @param {Array<string>} fields 列字段，顺序与表头一致
 */
function replaceTableBody(items, fields) {
    const tbody = document.querySelector('#data-table tbody');
    if (!tbody) return;

    const fragment = document.createDocumentFragment();
    (items || []).forEach(item => {
        const tr = document.createElement('tr');
        fields.forEach(field => {
            const td = document.createElement('td');
            const val = item[field];
            td.textContent = (val === null || val === undefined) ? '' : val;
            tr.appendChild(td);
        });
        fragment.appendChild(tr);
    });

    tbody.innerHTML = '';
    tbody.appendChild(fragment);
}


/**
 * 首次「按钮加载」完成后切换到已加载视图。
 * 页面初始为占位提示（load-placeholder）+ 隐藏表格/分页，避免打开页面就拉全量数据；
 * 任一查询（加载/搜索/翻页）返回后调用本函数恢复表格显示。
 * @param {number} rowCount 本次返回的行数，用于决定是否显示「没有找到匹配的数据」
 */
function revealLoadedTable(rowCount) {
    const placeholder = document.getElementById('load-placeholder');
    if (placeholder) placeholder.classList.add('d-none');

    const table = document.getElementById('data-table');
    if (table) table.classList.remove('d-none');

    const nav = document.getElementById('pagination-nav');
    if (nav) nav.classList.remove('d-none');

    const empty = document.getElementById('empty-state');
    if (empty) empty.classList.toggle('d-none', (rowCount || 0) > 0);
}


function clearSearchTimeoutNotice() {
    const old = document.getElementById('search-timeout-notice');
    if (old) old.remove();
}


/**
 * 大表查询超时提示。
 * 后端对数据页查询设置了时间预算（sub_list/usage_list 百万行级大表，极端模糊条件
 * 需要整表扫描数秒到数十秒）；超时会返回 search_timeout=true 且 items 为空。
 * 此处明确提示「不是没有数据，而是查询被中止」，引导用户收窄筛选条件。
 */
function showSearchTimeoutNotice() {
    const empty = document.getElementById('empty-state');
    if (empty) empty.classList.add('d-none'); // 超时不代表无数据，避免误报「没有找到」
    const old = document.getElementById('search-timeout-notice');
    if (old) old.remove();
    const alertHtml = `
        <div id="search-timeout-notice" class="alert alert-warning alert-dismissible fade show" role="alert">
            本次查询数据量较大，已超过等待上限被中止。请增加更精确的筛选条件（如完整的品番/图纸号）后重试。
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        </div>`;
    const container = document.querySelector('.card-body');
    if (container) container.insertAdjacentHTML('afterbegin', alertHtml);
}


/**
 * 统一更新分页控件（供 sub_list / usage_list 等 AJAX 局部刷新页面复用）
 * 修复：此前仅更新带 .pagination-info 的文本，且模板缺少该类导致整体为空操作，
 * 造成 AJAX 搜索/翻页后「共 X 条记录 / 共 Y 页」与跳转框 max 仍为旧值（数量、页码显示错误）。
 * 现同步更新：记录数标签、跳转输入框 max/value、页码链接（含上一页/下一页禁用态）。
 * @param {Object} pagination 后端返回的分页信息 { page, per_page, total, pages, has_prev, has_next }
 * @param {string} endpoint   当前页面路由，用于重建页码链接（保留现有查询串）
 */
function updatePaginationControls(pagination, endpoint) {
    if (!pagination) return;
    // total 为 null/undefined 表示服务端总数统计超时（大表），此时只展示上一页/下一页
    const totalKnown = pagination.total !== null && pagination.total !== undefined;
    const total = totalKnown ? pagination.total : null;
    const page = pagination.page || 1;
    const pages = totalKnown ? Math.max(1, pagination.pages || 1) : null;
    const hasNext = pagination.has_next !== undefined
        ? !!pagination.has_next
        : (pages ? page < pages : false);

    // 1) 记录数/页码文本
    const totalEl = document.querySelector('.pagination-info');
    if (totalEl) {
        totalEl.textContent = totalKnown
            ? `共 ${total} 条记录，当前第 ${page} 页 / 共 ${pages} 页`
            : `共 -- 条记录（数据量较大，已省略总数统计），当前第 ${page} 页`;
    }

    // 2) 跳转输入框：同步 max 与当前页，避免用旧 max 跳到不存在的页码
    const jumpInput = document.getElementById('jumpToPage');
    if (jumpInput) {
        if (pages) {
            jumpInput.max = String(pages);
        } else {
            jumpInput.removeAttribute('max');
        }
        jumpInput.value = String(page);
    }

    // 3) 页码链接：整体重建，确保页码数量随新结果变化（如从 17143 页缩到 7 页）
    const list = document.getElementById('pagination-list');
    if (!list) return;

    // 用当前地址栏的查询参数作为模板，保留搜索条件（page 由各链接单独覆盖）
    const baseParams = new URLSearchParams(window.location.search);
    baseParams.delete('page'); // page 由每个链接单独设置
    const buildHref = (targetPage) => {
        const p = new URLSearchParams(baseParams);
        p.set('page', String(targetPage));
        return `${endpoint}?${p.toString()}`;
    };
    // 与后端 PaginationHelper.iter_pages 保持一致：省略号用 null 表示
    // 后端参数 left_edge=1, left_current=2, right_current=2, right_edge=1
    const nums = [];
    const push = (n) => { if (n >= 1 && (!pages || n <= pages) && !nums.includes(n)) nums.push(n); };
    if (pages) {
        push(1);                                                          // left_edge=1 → 首 1 页
        for (let x = Math.max(1, page - 2); x <= Math.min(pages, page + 2); x++) push(x); // 当前页左右各 2 页
        push(pages);                                                      // right_edge=1 → 末 1 页
        nums.sort((a, b) => a - b);
    } else {
        push(page);                                                       // 总数未知：仅显示当前页
    }

    // 生成页码项（插入 null 占位生成省略号）
    const seq = [];
    let prev = 0;
    nums.forEach((n) => {
        if (prev && n - prev > 1) seq.push(null);
        seq.push(n);
        prev = n;
    });

    const makeLi = (html, cls) => {
        const li = document.createElement('li');
        li.className = `page-item${cls ? ' ' + cls : ''}`;
        li.innerHTML = html;
        return li;
    };
    const linkHtml = (target, label, aria) =>
        `<a class="page-link"${aria ? ` aria-label="${aria}"` : ''} data-page="${target}" ` +
        `href="${buildHref(target)}" style="padding: 0.375rem 0.75rem;">${label}</a>`;

    const fragment = document.createDocumentFragment();
    // 上一页
    fragment.appendChild(
        page > 1
            ? makeLi(linkHtml(page - 1, '&laquo;', '上一页'))
            : makeLi('<span class="page-link" style="padding: 0.375rem 0.75rem;">&laquo;</span>', 'disabled')
    );
    // 数字页与省略号
    seq.forEach((n) => {
        if (n === null) {
            fragment.appendChild(makeLi('<span class="page-link" style="padding: 0.375rem 0.75rem;">...</span>', 'disabled'));
        } else {
            fragment.appendChild(
                makeLi(linkHtml(n, String(n)), n === page ? 'active' : '')
            );
        }
    });
    // 下一页
    fragment.appendChild(
        hasNext
            ? makeLi(linkHtml(page + 1, '&raquo;', '下一页'))
            : makeLi('<span class="page-link" style="padding: 0.375rem 0.75rem;">&raquo;</span>', 'disabled')
    );

    list.innerHTML = '';
    list.appendChild(fragment);
}


/**
 * 页面初始化函数 - SubList
 */
function initSubListPage(options = {}) {
    const {
        searchFormId = 'search-form',
        tableId = 'data-table',
        searchInputIds = ['VehicleType', 'SubPno', 'EoKsc', 'SetCode', 'Circuit', 
                         'Wire', 'WireCode', 'Color', 'SQ', 'Length_Cut',
                         'Length_Cut2', 'Tml1', 'Tml2', 'Rel1', 'Rel2',
                         'Seal1', 'Seal2', 'Remark1'],
        onSearch = null
    } = options;
    
    const form = document.getElementById(searchFormId);
    const table = document.getElementById(tableId);
    
    if (!form || !table) return;
    
    // 性能修复：服务端已完成首屏渲染，这里不再用 TableRenderer 二次接管表格。
    // 原先在每次页面加载时都 new TableRenderer({virtualScroll:true}) 重建行渲染，
    // 既丢弃了服务端渲染结果又产生额外 DOM 操作，是切页卡顿的主要来源之一。
    // 页大小 5~50，无需虚拟滚动；仅在 AJAX 局部刷新时直接重绘 tbody。
    let abortController = null;
    
    async function performSearch(params = {}) {
        // 标记为「显式加载」：服务端据此才真正查库（未带该标记的首次进入不查询）
        params = Object.assign({ loaded: '1' }, params);
        // 取消上一次未完成的请求，避免旧响应覆盖新结果（竞态修复）
        if (abortController) {
            abortController.abort();
        }
        abortController = new AbortController();
        clearSearchTimeoutNotice();
        
        window.pageLoader.showLoading();
        
        try {
            const response = await window.dataLoader.loadData(
                '/sub_list',
                params,
                { signal: abortController.signal, forceRefresh: true }
            );
            
            if (response && response.items) {
                replaceTableBody(response.items, SUBLIST_FIELDS);
                revealLoadedTable(response.items.length);
                if (response.search_timeout) showSearchTimeoutNotice();
                
                // 更新分页信息
                if (response.pagination) {
                    updatePagination(response.pagination);
                }
                // 同步地址栏与分页链接，保证翻页/刷新保留当前搜索条件
                syncSearchState(params);
            }
        } catch (error) {
            if (error.name === 'AbortError') return; // 被新搜索取消，忽略
            console.error('搜索失败:', error);
            showError('搜索失败，请重试');
        } finally {
            window.pageLoader.hideLoading();
        }
    }
    
    // 搜索后同步浏览器地址栏与分页链接，保留当前搜索条件
    function syncSearchState(params = {}) {
        const url = new URL(window.location.pathname, window.location.origin);
        Object.entries(params).forEach(([key, val]) => {
            if (val !== '' && val !== null && val !== undefined) {
                url.searchParams.set(key, val);
            }
        });
        history.replaceState(null, '', url.toString());
        
        document.querySelectorAll('.pagination .page-link[href]').forEach(link => {
            const linkUrl = new URL(link.getAttribute('href'), window.location.origin);
            Object.entries(params).forEach(([key, val]) => {
                if (val !== '' && val !== null && val !== undefined) {
                    linkUrl.searchParams.set(key, val);
                }
            });
            link.setAttribute('href', linkUrl.toString());
        });
    }
    
    // 更新分页控件
    function updatePagination(pagination) {
        updatePaginationControls(pagination, '/sub_list');
    }
    
    // 显示错误
    function showError(message) {
        const alertHtml = `
            <div class="alert alert-danger alert-dismissible fade show" role="alert">
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            </div>
        `;
        const container = document.querySelector('.card-body');
        if (container) {
            container.insertAdjacentHTML('afterbegin', alertHtml);
        }
    }
    
    // 收集搜索参数
    function collectSearchParams() {
        const params = {};
        searchInputIds.forEach(id => {
            const input = document.getElementById(id);
            if (input && input.value.trim()) {
                params[id] = input.value.trim();
            }
        });
        return params;
    }
    
    // 读取「已生效」的搜索条件：搜索成功后 syncSearchState 会把生效条件同步进地址栏，
    // 因此地址栏才是与当前表格数据一致的口径。下载必须用它，
    // 否则会带上用户尚未点击搜索的临时输入（或未 trim 的空白）导致导出 0 条。
    function collectEffectiveSearchParams() {
        const urlParams = new URLSearchParams(window.location.search);
        const params = {};
        searchInputIds.forEach(id => {
            const value = (urlParams.get(id) || '').trim();
            if (value) params[id] = value;
        });
        return params;
    }
    
    // 搜索框实时搜索（防抖）由各页面模板按需调用，避免无谓的 input 监听
    
    return {
        search: performSearch,
        collectSearchParams: collectSearchParams,
        collectEffectiveSearchParams: collectEffectiveSearchParams
    };
}


/**
 * 页面初始化函数 - UsageList
 */
function initUsageListPage(options = {}) {
    const {
        searchFormId = 'search-form',
        tableId = 'data-table',
        searchInputIds = ['VehicleType', 'SubPno', 'EoKsc', 'Comp_Kind', 'Detail_Kind',
                         'KscPno', 'Part_Name', 'Usage', 'Length'],
        onSearch = null
    } = options;
    
    const form = document.getElementById(searchFormId);
    const table = document.getElementById(tableId);
    
    if (!form || !table) return;
    
    // 性能修复：同 initSubListPage，不再二次接管服务端已渲染的表格
    let abortController = null;
    
    async function performSearch(params = {}) {
        // 标记为「显式加载」：服务端据此才真正查库（未带该标记的首次进入不查询）
        params = Object.assign({ loaded: '1' }, params);
        // 取消上一次未完成的请求，避免旧响应覆盖新结果（竞态修复）
        if (abortController) {
            abortController.abort();
        }
        abortController = new AbortController();
        clearSearchTimeoutNotice();
        
        window.pageLoader.showLoading();
        
        try {
            const response = await window.dataLoader.loadData(
                '/usage_list',
                params,
                { signal: abortController.signal, forceRefresh: true }
            );
            
            if (response && response.items) {
                replaceTableBody(response.items, USAGELIST_FIELDS);
                revealLoadedTable(response.items.length);
                if (response.search_timeout) showSearchTimeoutNotice();
                
                if (response.pagination) {
                    updatePaginationControls(response.pagination, '/usage_list');
                }
                // 同步地址栏与分页链接，保证翻页/刷新保留当前搜索条件
                syncSearchState(params);
            }
        } catch (error) {
            if (error.name === 'AbortError') return; // 被新搜索取消，忽略
            console.error('搜索失败:', error);
        } finally {
            window.pageLoader.hideLoading();
        }
    }
    
    // 搜索后同步浏览器地址栏与分页链接，保留当前搜索条件
    function syncSearchState(params = {}) {
        const url = new URL(window.location.pathname, window.location.origin);
        Object.entries(params).forEach(([key, val]) => {
            if (val !== '' && val !== null && val !== undefined) {
                url.searchParams.set(key, val);
            }
        });
        history.replaceState(null, '', url.toString());
        
        document.querySelectorAll('.pagination .page-link[href]').forEach(link => {
            const linkUrl = new URL(link.getAttribute('href'), window.location.origin);
            Object.entries(params).forEach(([key, val]) => {
                if (val !== '' && val !== null && val !== undefined) {
                    linkUrl.searchParams.set(key, val);
                }
            });
            link.setAttribute('href', linkUrl.toString());
        });
    }
    
    // 收集搜索参数
    function collectSearchParams() {
        const params = {};
        searchInputIds.forEach(id => {
            const input = document.getElementById(id);
            if (input && input.value.trim()) {
                params[id] = input.value.trim();
            }
        });
        return params;
    }
    
    // 读取「已生效」的搜索条件（口径同 initSubListPage，供下载使用）
    function collectEffectiveSearchParams() {
        const urlParams = new URLSearchParams(window.location.search);
        const params = {};
        searchInputIds.forEach(id => {
            const value = (urlParams.get(id) || '').trim();
            if (value) params[id] = value;
        });
        return params;
    }
    
    // 搜索框实时搜索（防抖）由各页面模板按需调用，避免无谓的 input 监听
    
    return {
        search: performSearch,
        collectSearchParams: collectSearchParams,
        collectEffectiveSearchParams: collectEffectiveSearchParams
    };
}


// 导出到全局
window.initSubListPage = initSubListPage;
window.initUsageListPage = initUsageListPage;
window.DataLoaderManager = DataLoaderManager;
window.TableRenderer = TableRenderer;
window.PageLoadingManager = PageLoadingManager;
