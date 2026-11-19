/* ============================================================
 * 顶部标签页导航栏（Tab Bar）交互
 * 职责：
 *   1) 打开标签：点击左侧栏带 data-tab-title 的菜单链接 → 新增/激活标签（改走 SPA 局部加载）
 *   2) 切换标签：点击标签 → 局部加载目标页面、不整页刷新
 *   3) 关闭标签：× 按钮 / 右键菜单「关闭」「关闭其他」「关闭全部」
 *   4) 持久化：标签列表与当前激活项写入 localStorage；用户主动刷新（F5 / 刷新按钮 / Ctrl+R）时按「关闭全部」处理
 *      ——清空全部标签并回到首页（避免刷新后旧工作区被自动恢复）
 *      应用内主动导航（表单提交、同址跳转、标签切换）会先打标记，不按刷新处理
 *   5) 拖拽排序：HTML5 拖放调整顺序并持久化
 *   6) 兜底：不做 SPA、Ctrl/Cmd 点击、无 JS 加载能力时一律回退为整页跳转
 *
 * 说明：本脚本在 _tabbar.html 中「同步」引入，DOM（标签栏骨架）位于脚本之前，
 * 因此首帧即可渲染已恢复的标签，避免"先空白后补标签"的闪烁。
 * ============================================================ */
(function () {
    'use strict';

    var STORAGE_KEY = 'appTabbarState';
    var LOAD_MARK_KEY = 'appTabbarLastLoad';      // sessionStorage：本标签页上次加载的地址
    var NAV_MARK_KEY = 'appTabbarNavMark';        // sessionStorage：应用内主动导航的时间戳
    /* 导航标记有效期要覆盖耗时较长的表单提交（如批量上传大 Excel 后重定向回本页），
     * 因此不做定时清除，只在下次加载时消费；超期未消费的冗余标记会被忽略。 */
    var NAV_MARK_TTL = 10 * 60 * 1000;
    var MAX_TABS = 15;            // 标签数量上限，超出时关闭最久未使用的（首页除外）
    var HOME_URL = null;          // 首页路径（不可关闭的兜底标签）

    var root = document.documentElement;
    var body = document.body;

    var bar = document.getElementById('appTabbar');
    var scrollEl = document.getElementById('appTabbarScroll');
    var menuEl = document.getElementById('appTabbarMenu');
    if (!bar || !scrollEl) { return; }

    /* ---------- 路由元数据：URL → 中文标题 ---------- */
    var routes = window.__TABBAR_ROUTES__ || [];
    var titleByUrl = {};
    routes.forEach(function (r) {
        titleByUrl[normalize(r.url)] = r.title;
        if (!HOME_URL) { HOME_URL = normalize(r.url); } // 首个即首页
    });

    /* 归一化路径：统一以 / 开头、去掉结尾斜杠与查询串，便于比对 */
    function normalize(url) {
        if (!url) { return ''; }
        var path = url.split('#')[0].split('?')[0];
        if (path.charAt(0) !== '/') { path = '/' + path; }
        if (path.length > 1 && path.charAt(path.length - 1) === '/') { path = path.slice(0, -1); }
        return path;
    }

    /* 由 URL 推导标题：优先路由表，其次菜单文案，最后回退到路径末段 */
    function resolveTitle(url) {
        var key = normalize(url);
        return titleByUrl[key] || decodeURIComponent(key.split('/').pop() || '页面');
    }

    /* 标记「本次加载由应用内主动导航引起」（表单提交 / 同页链接 / 编程跳转）。
     * 这类跳转常常落回同一地址（如 GET 筛选、POST 后重定向），不能当成刷新。
     * 只写入时间戳，不清除：服务器处理耗时（大文件入库可达数十秒）期间页面仍存活，
     * 若用定时器清除会在线程等待中被误删，导致跳转回来又被判为刷新。 */
    function markNavigation() {
        try { sessionStorage.setItem(NAV_MARK_KEY, String(Date.now())); } catch (e) { /* 隐私模式忽略 */ }
    }

    /* 读取并清除指定标记，返回该标记是否在有效期内 */
    function consumeMark(key, ttl) {
        try {
            var raw = sessionStorage.getItem(key);
            if (!raw) { return false; }
            sessionStorage.removeItem(key);
            return (Date.now() - Number(raw)) <= ttl;
        } catch (e) { return false; }
    }

    /* 判断本次页面加载是否属于「刷新」（F5 / 刷新按钮 / Ctrl+R）。
     * 主判据用 Navigation Timing（type==='reload'）；
     * 部分内嵌浏览器/自动化环境会把刷新上报成 'navigate'，因此保留兜底：
     * 上一次加载记录的地址与本次相同 → 视为刷新；但应用内主动导航已打标记，优先排除。
     * 产品约定刷新即主动重置工作区，应关闭全部标签。 */
    function isPageReload(currentUrl) {
        var navType = '';
        try {
            var entries = performance.getEntriesByType('navigation');
            if (entries.length) { navType = entries[0].type; }
        } catch (e) { /* 不支持时走旧 API 兜底 */ }
        if (!navType) {
            /* 旧版浏览器（Win7 上的老 Chrome/IE11）兜底：type===1 即 TYPE_RELOAD */
            if (typeof performance.navigation === 'object' && performance.navigation !== null) {
                navType = performance.navigation.type === 1 ? 'reload' : 'navigate';
            }
        }

        /* 无论结论如何都先消费标记，避免残留影响下一次判定 */
        var navMarked = consumeMark(NAV_MARK_KEY, NAV_MARK_TTL);

        /* reload 类型 = 用户主动刷新（脚本 location.reload() 也归此类，浏览器层面无法区分） */
        if (navType === 'reload') { return true; }
        /* 应用内主动导航（表单提交 / 同址跳转 / 标签切换）：即使地址相同也不是刷新 */
        if (navMarked) { return false; }

        try {
            var lastUrl = sessionStorage.getItem(LOAD_MARK_KEY);
            if (lastUrl && normalize(lastUrl) === currentUrl) { return true; }
        } catch (e) { /* 隐私模式忽略 */ }
        return false;
    }

    /* 记录本次加载的地址（供下次刷新判断用）；sessionStorage 随标签页关闭而清空 */
    function markLoad(url) {
        try { sessionStorage.setItem(LOAD_MARK_KEY, url); } catch (e) { /* 隐私模式忽略 */ }
    }

    /* ---------- 状态：{ tabs: [{url,title}], active: url } ---------- */
    var state = { tabs: [], active: '' };

    function loadState() {
        try {
            var raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) { return null; }
            var parsed = JSON.parse(raw);
            if (!parsed || !Array.isArray(parsed.tabs)) { return null; }
            var seen = {};
            var tabs = parsed.tabs.filter(function (t) {
                if (!t || !t.url) { return false; }
                var key = normalize(t.url);
                if (seen[key]) { return false; }  // 去重：防止历史脏数据产生重复标签
                seen[key] = true;
                return true;
            }).map(function (t) { return { url: normalize(t.url), title: t.title || resolveTitle(t.url) }; });
            return { tabs: tabs, active: normalize(parsed.active || '') };
        } catch (e) { return null; }
    }

    function saveState() {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (e) { /* 隐私模式忽略 */ }
    }

    /* ---------- 渲染 ---------- */
    function findTabIndex(url) {
        var key = normalize(url);
        for (var i = 0; i < state.tabs.length; i++) {
            if (state.tabs[i].url === key) { return i; }
        }
        return -1;
    }

    /* 去重：保证任意时刻标签列表中同一 URL 只保留一项（保留首次出现的位置与标题） */
    function dedupeTabs() {
        var seen = {};
        var result = [];
        state.tabs.forEach(function (t) {
            var key = normalize(t.url);
            if (!key || seen[key]) { return; }
            seen[key] = true;
            t.url = key;
            result.push(t);
        });
        state.tabs = result;
    }

    function render() {
        dedupeTabs();  // 渲染前兜底去重，杜绝任何来源的重复标签
        var frag = document.createDocumentFragment();

        state.tabs.forEach(function (tab, index) {
            var el = document.createElement('a');
            el.className = 'app-tab' + (tab.url === state.active ? ' is-active' : '');
            el.href = tab.url;
            el.setAttribute('draggable', 'true');
            el.dataset.url = tab.url;
            el.dataset.index = String(index);
            /* 只保留"首页"不可关闭？——按需求：所有标签均可关闭 */
            el.title = tab.title;

            var title = document.createElement('span');
            title.className = 'app-tab-title';
            title.textContent = tab.title;
            el.appendChild(title);

            var close = document.createElement('button');
            close.type = 'button';
            close.className = 'app-tab-close';
            close.setAttribute('aria-label', '关闭标签 ' + tab.title);
            close.innerHTML = '&times;';
            close.dataset.close = tab.url;
            el.appendChild(close);

            frag.appendChild(el);
        });

        scrollEl.innerHTML = '';
        scrollEl.appendChild(frag);
        syncNavHighlight();
    }

    /* ---------- 标签增删改 ---------- */
    /* 打开（新增或激活）指定 URL 的标签；不触发跳转，仅更新状态与视图 */
    function openTab(url, title, options) {
        url = normalize(url);
        var opts = options || {};
        dedupeTabs();  // 先清理历史重复项，避免索引错乱
        var index = findTabIndex(url);

        if (index === -1) {
            /* 超限时移除最久未使用的非当前、非首页标签 */
            if (state.tabs.length >= MAX_TABS) {
                var victim = state.tabs.findIndex(function (t) {
                    return t.url !== state.active && t.url !== HOME_URL;
                });
                if (victim !== -1) { state.tabs.splice(victim, 1); }
            }
            state.tabs.push({ url: url, title: title || resolveTitle(url) });
        } else if (title) {
            state.tabs[index].title = title;
        }

        state.active = url;
        dedupeTabs();
        saveState();
        if (!opts.silent) { render(); }
    }

    /* 关闭指定标签；关闭当前项时激活相邻标签（优先右邻，其次左邻） */
    function closeTab(url) {
        url = normalize(url);
        var index = findTabIndex(url);
        if (index === -1) { return; }

        var wasActive = state.active === url;
        state.tabs.splice(index, 1);

        if (wasActive) {
            var next = state.tabs[index] || state.tabs[index - 1] || null;
            if (next) {
                state.active = next.url;
                saveState();          // 必须先持久化，否则刷新后已关闭的标签会"复活"
                navigate(next.url);   // 当前标签被关闭 → 跳转到相邻标签
                return;
            }
            state.active = '';
        }
        saveState();
        render();
        /* 关闭后已无任何标签：回到首页兜底 */
        if (!state.tabs.length) { navigate(HOME_URL || '/'); }
    }

    function closeOthers(url) {
        url = normalize(url);
        state.tabs = state.tabs.filter(function (t) { return t.url === url; });
        state.active = url;
        saveState();
        render();
    }

    function closeAll() {
        state.tabs = [];
        state.active = '';
        saveState();
        render();
        navigate(HOME_URL || '/');
    }

    /* ---------- 页面跳转（整页） ---------- */
    function navigate(url) {
        markNavigation();   // 标签切换/菜单跳转属应用内主动跳转，不算刷新
        window.location.href = url;
    }

    /* 应用内整页刷新：先打跳转标记，再用 replace 重新加载当前地址。
     * 浏览器会把 location.reload() 上报成 reload 类型（与用户 F5 无法区分），
     * 而整页重定向上报的是普通导航，配合标记即可保留工作区标签。 */
    function refresh() {
        markNavigation();
        var href = window.location.href;
        try {
            window.location.replace(href);   // 不留历史记录，语义等同刷新
        } catch (e) {
            window.location.href = href;
        }
    }

    /* ---------- 左侧栏联动高亮 ---------- */
    /* 仅同步激活态样式，不改变分组的展开/收起状态——展开状态由用户控制并持久化在 appSidebarOpenGroups，
     * 若在此强制展开，会导致用户手动收起的分组每次切换标签都被"自动展开"。 */
    function syncNavHighlight() {
        var links = document.querySelectorAll('.app-sidebar .app-nav-link');
        for (var i = 0; i < links.length; i++) {
            var href = links[i].getAttribute('href') || '';
            /* 分组标题的 href 是 "#"，normalize 后会变成 '/'，
             * 在首页会误命中所有分组——占位链接不参与高亮 */
            var isSame = href && href !== '#' && normalize(href) === state.active;
            links[i].classList.toggle('active', !!isSame);
        }
    }

    /* ---------- 事件：标签点击 / 关闭 ---------- */
    scrollEl.addEventListener('click', function (e) {
        var closeBtn = e.target.closest('.app-tab-close');
        if (closeBtn) {
            e.preventDefault();
            e.stopPropagation();
            closeTab(closeBtn.dataset.close);
            return;
        }

        var tab = e.target.closest('.app-tab');
        if (!tab) { return; }

        var url = tab.dataset.url;
        /* Ctrl/Cmd/中键：交给浏览器新标签页打开，不接管 */
        if (e.ctrlKey || e.metaKey || e.shiftKey) { return; }
        e.preventDefault();
        if (state.active === url) { return; }

        state.active = url;
        saveState();
        render();
        navigate(url);
    });

    /* 中键关闭标签 */
    scrollEl.addEventListener('auxclick', function (e) {
        if (e.button !== 1) { return; }
        var tab = e.target.closest('.app-tab');
        if (!tab) { return; }
        e.preventDefault();
        closeTab(tab.dataset.url);
    });

    /* ---------- 事件：右键菜单 ---------- */
    var menuTargetUrl = '';

    function showMenu(x, y, url) {
        menuTargetUrl = url;
        var onlyOne = state.tabs.length <= 1;
        var isLast = state.tabs.length === 1 && state.tabs[0].url === url;

        menuEl.innerHTML = '';
        menuEl.appendChild(menuButton('关闭当前', function () { closeTab(url); }, false));
        menuEl.appendChild(menuButton('关闭其他', function () { closeOthers(url); }, onlyOne));
        menuEl.appendChild(menuButton('关闭全部', function () { closeAll(); }, false));
        var sep = document.createElement('div');
        sep.className = 'menu-sep';
        menuEl.appendChild(sep);
        menuEl.appendChild(menuButton('刷新当前页', function () { refresh(); }, false));

        menuEl.classList.add('show');
        menuEl.setAttribute('aria-hidden', 'false');

        /* 视口内定位，避免菜单溢出屏幕 */
        var rect = menuEl.getBoundingClientRect();
        var left = Math.min(x, window.innerWidth - rect.width - 8);
        var top = Math.min(y, window.innerHeight - rect.height - 8);
        menuEl.style.left = Math.max(8, left) + 'px';
        menuEl.style.top = Math.max(8, top) + 'px';
        void isLast;
    }

    function menuButton(label, handler, disabled) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = label;
        btn.disabled = !!disabled;
        btn.addEventListener('click', function () {
            hideMenu();
            handler();
        });
        return btn;
    }

    function hideMenu() {
        menuEl.classList.remove('show');
        menuEl.setAttribute('aria-hidden', 'true');
    }

    scrollEl.addEventListener('contextmenu', function (e) {
        var tab = e.target.closest('.app-tab');
        if (!tab) { return; }
        e.preventDefault();
        showMenu(e.clientX, e.clientY, tab.dataset.url);
    });

    document.addEventListener('click', function (e) {
        if (!menuEl.contains(e.target)) { hideMenu(); }
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { hideMenu(); }
    });
    window.addEventListener('resize', hideMenu);
    window.addEventListener('scroll', hideMenu, true);

    /* ---------- 事件：拖拽排序 ---------- */
    var dragUrl = '';

    scrollEl.addEventListener('dragstart', function (e) {
        var tab = e.target.closest('.app-tab');
        if (!tab) { return; }
        dragUrl = tab.dataset.url;
        tab.classList.add('is-dragging');
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', dragUrl); } catch (err) { /* IE 兼容忽略 */ }
    });

    scrollEl.addEventListener('dragover', function (e) {
        var tab = e.target.closest('.app-tab');
        if (!tab || !dragUrl) { return; }
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';

        /* 清除旧指示，按鼠标位于目标标签左/右半边决定插入方向 */
        scrollEl.querySelectorAll('.drop-before, .drop-after').forEach(function (el) {
            el.classList.remove('drop-before', 'drop-after');
        });
        if (tab.dataset.url === dragUrl) { return; }
        var rect = tab.getBoundingClientRect();
        tab.classList.add(e.clientX < rect.left + rect.width / 2 ? 'drop-before' : 'drop-after');
    });

    scrollEl.addEventListener('drop', function (e) {
        var tab = e.target.closest('.app-tab');
        if (!tab || !dragUrl) { return; }
        e.preventDefault();

        var from = findTabIndex(dragUrl);
        var to = findTabIndex(tab.dataset.url);
        if (from === -1 || to === -1 || from === to) { cleanupDrag(); return; }

        var before = tab.classList.contains('drop-before');
        var moved = state.tabs.splice(from, 1)[0];
        /* 移除源项后目标索引可能左移 1 位 */
        var insertAt = findTabIndex(tab.dataset.url);
        insertAt += before ? 0 : 1;
        state.tabs.splice(insertAt, 0, moved);

        saveState();
        cleanupDrag();
        render();
    });

    scrollEl.addEventListener('dragend', cleanupDrag);

    function cleanupDrag() {
        dragUrl = '';
        scrollEl.querySelectorAll('.app-tab').forEach(function (el) {
            el.classList.remove('is-dragging', 'drop-before', 'drop-after');
        });
    }

    /* ---------- 事件：左侧栏菜单点击 → 新增标签 ---------- */
    /* 通过事件委托绑定在 document 上，兼容侧栏后于本脚本渲染/存在多个导航区的情况 */
    document.addEventListener('click', function (e) {
        var link = e.target.closest('a[data-tab-title]');
        if (!link) { return; }
        var href = link.getAttribute('href') || '';
        if (!href || href.charAt(0) === '#') { return; }
        /* 修饰键 / 非左键：保留浏览器默认新窗口行为 */
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button !== 0) { return; }

        e.preventDefault();
        var url = normalize(href);
        openTab(url, link.dataset.tabTitle);
        navigate(url);
    });

    /* ---------- 供其它脚本调用：手动标记一次应用内跳转（下一跳不会被当作刷新） ---------- */
    window.AppTabbar = {
        open: openTab,
        close: closeTab,
        closeOthers: closeOthers,
        closeAll: closeAll,
        markNav: markNavigation,
        refresh: refresh,
        getState: function () { return JSON.parse(JSON.stringify(state)); }
    };
    /* 全局整页刷新入口：业务页面一律用它，不要再用 location.reload() */
    window.appRefreshPage = refresh;

    /* ---------- 自动打「跳转标记」：应用内的同址跳转不应被误判为刷新 ---------- */
    /* 1) 常规表单提交：GET 筛选、POST 入库/删除/重新上传（重定向回本页） */
    document.addEventListener('submit', markNavigation, true);
    /* 2) 编程式 form.submit()（如 onchange="this.form.submit()"）不触发 submit 事件，需在原型上兜底 */
    if (window.HTMLFormElement && HTMLFormElement.prototype.submit) {
        var nativeFormSubmit = HTMLFormElement.prototype.submit;
        HTMLFormElement.prototype.submit = function () {
            markNavigation();
            return nativeFormSubmit.apply(this, arguments);
        };
    }
    /* 3) 同页链接：仅改变查询参数的同一地址（如日历上/下月、分页），归一化后与当前页相同 */
    document.addEventListener('click', function (e) {
        var link = e.target && e.target.closest ? e.target.closest('a[href]') : null;
        if (!link) { return; }
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button !== 0) { return; }
        if (link.closest && link.closest('.app-tabbar')) { return; }   // 标签栏自身由 navigate 负责标记
        if (link.target && link.target !== '_self') { return; }
        if (link.hasAttribute('download')) { return; }
        var href = link.getAttribute('href') || '';
        if (!href || href.charAt(0) === '#' || /^(javascript|mailto|tel):/i.test(href)) { return; }
        if (link.origin && link.origin !== window.location.origin) { return; }
        if (link.pathname && normalize(link.pathname) === normalize(window.location.pathname)) {
            markNavigation();
        }
    }, true);

    /* ---------- 首屏初始化 ---------- */
    (function init() {
        var restored = loadState();
        var currentUrl = normalize(window.location.pathname);

        if (restored && restored.tabs.length) {
            state = restored;
        }

        /* 浏览器刷新（F5 / 刷新按钮）= 主动重置工作区：
         * 按右键菜单「关闭全部」的语义处理——清空所有标签后回到首页，
         * 首页作为唯一保留的兜底标签，避免出现无标签可落的空状态。
         * location.replace 不留历史记录，防止后退又退回被清空前的工作区。 */
        if (isPageReload(currentUrl)) {
            var homeUrl = normalize(HOME_URL || '/');
            state.tabs = [];
            state.active = '';
            if (currentUrl !== homeUrl) {
                saveState();                      // 先落盘：否则首页加载时又会把旧标签恢复回来
                window.location.replace(homeUrl);
                return;  /* 即将跳转到首页，本次渲染与骨架迁移无意义 */
            }
            /* 已在首页刷新：清空后仅保留首页一个标签（由下方统一逻辑补回） */
        }

        /* 当前页面不在标签列表中：追加（直接输入地址/普通链接进入时） */
        if (findTabIndex(currentUrl) === -1) {
            state.tabs.push({ url: currentUrl, title: resolveTitle(currentUrl) });
        }
        state.active = currentUrl;

        saveState();
        render();
        /* 记录本次加载地址：下次在本标签页再次加载同一地址即判定为刷新 */
        markLoad(currentUrl);

        /* 首屏同步阶段关闭过渡，避免标签出现淡入抖动 */
        root.classList.add('app-tabbar-init');
        requestAnimationFrame(function () {
            requestAnimationFrame(function () { root.classList.remove('app-tabbar-init'); });
        });

        /* 骨架被放在 #appTabbarMount 挂载点内，须迁移到 body 顶部（紧跟侧栏之后）
         * 才能与左侧栏共同构成截图中的布局；迁移后视图已渲染，不会闪烁。 */
        var mount = document.getElementById('appTabbarMount');
        if (mount) {
            var fragment = document.createDocumentFragment();
            while (mount.firstChild) { fragment.appendChild(mount.firstChild); }
            body.insertBefore(fragment, body.firstChild);
            mount.parentNode.removeChild(mount);
        }
    })();
})();
