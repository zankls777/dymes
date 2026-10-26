    // 性能优化工具类
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

    // 收集搜索表单中所有非空参数(辅助函数)
    function _collectSearchParams() {
        const params = {};
        const form = document.getElementById('search-form');
        if (!form) return params;
        form.querySelectorAll('input[name], select[name], textarea[name]').forEach(input => {
            if (input.value && input.value.trim()) {
                params[input.name] = input.value.trim();
            }
        });
        return params;
    }

    // 每页数量变更:清除旧参数→收集搜索条件→重置页码→跳转
    function changePerPage(perPage) {
        const url = new URL(window.location.pathname, window.location.origin);
        const searchParams = _collectSearchParams();
        Object.keys(searchParams).forEach(key => {
            url.searchParams.set(key, searchParams[key]);
        });
        url.searchParams.set('per_page', String(perPage));
        url.searchParams.set('page', '1');
        window.location.href = url.toString();
    }

    // 跳转到指定页码:清除旧参数→收集搜索条件→设置目标页码→跳转
    function jumpToPage() {
        const input = document.getElementById('jumpToPage');
        if (!input) return;
        let page = parseInt(input.value);
        if (isNaN(page) || page < 1) page = 1;
        const maxPage = parseInt(input.getAttribute('max')) || 999999;
        if (page > maxPage) page = maxPage;
        const url = new URL(window.location.pathname, window.location.origin);
        const searchParams = _collectSearchParams();
        Object.keys(searchParams).forEach(key => {
            url.searchParams.set(key, searchParams[key]);
        });
        const perPageSelect = document.querySelector('.form-select');
        const currentPerPage = perPageSelect ? perPageSelect.value : '10';
        url.searchParams.set('per_page', currentPerPage);
        url.searchParams.set('page', String(page));
        window.location.href = url.toString();
    }

    // 表格行悬停效果：按容器委派，使用 mouseover/mouseout 并校验 relatedTarget，
    // 避免为每行绑定事件、避免 mouseenter/mouseleave 造成的频繁切换与重排
    function initTableHover() {
        const tables = document.querySelectorAll('table');
        tables.forEach(table => {
            table.addEventListener('mouseover', function(e) {
                const row = e.target.closest('tbody tr');
                if (row && row.parentNode === this.querySelector('tbody')) {
                    if (!row.classList.contains('bg-light')) row.classList.add('bg-light');
                }
            });
            table.addEventListener('mouseout', function(e) {
                const row = e.target.closest('tbody tr');
                if (!row) return;
                const to = e.relatedTarget;
                if (!to || !row.contains(to)) row.classList.remove('bg-light');
            });
        });
    }

    // 页面加载时初始化
    document.addEventListener('DOMContentLoaded', function() {
        // 懒加载图片
        window.PerfOptimizer.lazyLoadImages();

        // 切换模块时自动聚焦首个搜索框（仅在存在搜索表单时，避免无谓的元素扫描）
        const searchForm = document.getElementById('search-form');
        if (searchForm) {
            const firstInput = searchForm.querySelector('input[type="text"]');
            if (firstInput) firstInput.focus();
        }

        // 表格行悬停效果
        initTableHover();

        // 初始化响应式导航栏
        initNavbarResponsive();
        adjustLayout();
    });

    // 优化搜索框输入（防抖）
    function setupSearchDebounce() {
        const searchInputs = document.querySelectorAll('input[type="text"]');
        searchInputs.forEach(input => {
            const originalHandler = input.oninput;
            if (originalHandler) {
                input.oninput = window.PerfOptimizer.debounce(originalHandler, 300);
            }
        });
    }

    // 移动端导航栏交互增强
    function initNavbarResponsive() {
        const navbarCollapse = document.querySelector('.navbar-collapse');
        if (!navbarCollapse) return;

        // 导航链接点击后自动收起菜单（移动端）
        const navLinks = document.querySelectorAll('.navbar-nav .nav-link:not(.dropdown-toggle)');
        navLinks.forEach(link => {
            link.addEventListener('click', () => {
                if (window.innerWidth < 768 && navbarCollapse.classList.contains('show')) {
                    const collapse = new bootstrap.Collapse(navbarCollapse, { toggle: false });
                    collapse.hide();
                }
            });
        });

        // 下拉菜单项点击后自动收起菜单（移动端）
        const dropdownItems = document.querySelectorAll('.navbar-nav .dropdown-item');
        dropdownItems.forEach(item => {
            item.addEventListener('click', () => {
                if (window.innerWidth < 768 && navbarCollapse.classList.contains('show')) {
                    setTimeout(() => {
                        const collapse = new bootstrap.Collapse(navbarCollapse, { toggle: false });
                        collapse.hide();
                    }, 150);
                }
            });
        });

        // 窗口尺寸变化时重置导航栏状态
        let resizeTimer;
        window.addEventListener('resize', function() {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {
                if (window.innerWidth >= 768 && navbarCollapse.classList.contains('show')) {
                    const collapse = new bootstrap.Collapse(navbarCollapse, { toggle: false });
                    collapse.hide();
                }
            }, 200);
        });
    }

    // 优化窗口resize事件（节流）
    window.addEventListener('resize', window.PerfOptimizer.throttle(function() {
        // 响应式布局调整
        adjustLayout();
    }, 200));

    function adjustLayout() {
        // 响应式布局调整逻辑
        const width = window.innerWidth;
        if (width < 768) {
            // 移动端布局
        } else {
            // 桌面端布局
        }
    }
