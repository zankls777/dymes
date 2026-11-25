/* 应用侧边栏（导航栏）交互：
 * 1) 桌面端：#appSidebarCollapse 折叠/展开，状态持久化到 localStorage.appSidebarCollapsed
 * 2) 移动端：#appSidebarToggle 打开抽屉、#appSidebarBackdrop 点击遮罩关闭
 * 3) 折叠后：#appSidebarFloatBtn 固定按钮重新展开
 * 4) Alt+S 快捷键折叠/展开
 * 说明：首屏落位由 sidebar-init.js（同步脚本）+ CSS 完成，本文件只处理交互态，
 * 交互前追加 body.app-sidebar-anim 以启用过渡动画，避免首屏出现滑动白边。
 */
(function () {
    'use strict';

    var MOBILE_MAX = 767.98; // 与 CSS 断点保持一致
    var STORAGE_KEY = 'appSidebarCollapsed';

    function isMobile() {
        return window.innerWidth <= MOBILE_MAX;
    }

    function onReady(fn) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', fn);
        } else {
            fn();
        }
    }

    onReady(function () {
        var sidebar = document.getElementById('appSidebar');
        var collapseBtn = document.getElementById('appSidebarCollapse'); // 桌面端头部折叠按钮
        var toggleBtn = document.getElementById('appSidebarToggle');     // 移动端汉堡按钮
        var floatBtn = document.getElementById('appSidebarFloatBtn');    // 桌面折叠后固定展开按钮
        var backdrop = document.getElementById('appSidebarBackdrop');    // 移动端遮罩
        if (!sidebar) { return; }

        var body = document.body;
        var root = document.documentElement; // <html>：承载首屏同步类 app-sidebar-open-init / app-sidebar-collapsed-init

        /* 首屏同步已由 sidebar-init.js 完成（含分组展开），此处摘掉首屏标记，
         * 让后续用户点击分组时恢复正常过渡动画。用 rAF 保证在首帧渲染后再摘除。 */
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                root.classList.remove('app-sidebar-nav-init');
            });
        });

        /* 交互首次触发时启用动画（首屏不加过渡，直接落位） */
        function enableAnim() {
            body.classList.add('app-sidebar-anim');
        }

        function isCollapsed() {
            /* 移动端没有"桌面折叠"概念（抽屉开合由 .open 独立控制），
             * 恒返回 false，避免桌面持久化的折叠标记影响移动端抽屉展开 */
            if (isMobile()) { return false; }
            /* 以“折叠”为单一真源：任一折叠标记存在即视为折叠态 */
            return sidebar.classList.contains('collapsed')
                || root.classList.contains('app-sidebar-collapsed-init');
        }

        /* 写回折叠状态到 localStorage，供下次首屏同步脚本使用 */
        function persist(collapsed) {
            try { localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0'); } catch (e) { /* 隐私模式忽略 */ }
        }

        /* 同步 <html> 上的首屏同步类：交互后须清除，否则 CSS 的 padding 偏移不会跟随折叠状态 */
        function syncInitClasses(collapsed) {
            root.classList.remove('app-sidebar-open-init', 'app-sidebar-collapsed-init');
            if (!collapsed) {
                root.classList.add('app-sidebar-open-init'); // 展开态：保留类以驱动 padding-left
            }
        }

        /* 桌面端：应用折叠/展开的视觉状态与内容区偏移 */
        function applyDesktop(collapsed) {
            sidebar.classList.toggle('collapsed', collapsed);
            body.classList.toggle('app-sidebar-open', !collapsed);
            /* 折叠态为图标栏（宽 80px），body 需让出图标栏宽度，故单独打标记 */
            body.classList.toggle('app-sidebar-collapsed', collapsed);
            syncInitClasses(collapsed); // 关键：同步 html 类，消除折叠后残留的内容偏移
            /* 图标栏常显，固定悬浮展开按钮不再需要 */
            if (floatBtn) { floatBtn.classList.remove('show'); }
        }

        /* 移动端：抽屉打开/关闭 */
        function openMobile() {
            enableAnim();
            /* 兜底：清除桌面折叠标记（app-sidebar-collapsed-init 优先级高，
             * 会强制 translateX(-100%) 把抽屉压回屏幕外），否则移动端无法展开 */
            root.classList.remove('app-sidebar-collapsed-init');
            sidebar.classList.remove('collapsed');
            sidebar.classList.add('open');
            if (backdrop) { backdrop.classList.add('show'); }
        }
        function closeMobile() {
            sidebar.classList.remove('open');
            /* 同步移除桌面折叠类，保证下次 openMobile() 不被残留状态干扰 */
            sidebar.classList.remove('collapsed');
            if (backdrop) { backdrop.classList.remove('show'); }
        }

        /* 桌面折叠按钮：切换并持久化 */
        if (collapseBtn) {
            collapseBtn.addEventListener('click', function () {
                enableAnim();
                var next = !isCollapsed();
                applyDesktop(next);
                persist(next);
            });
        }

        /* 折叠态下固定展开按钮（仅桌面显示）：展开 */
        if (floatBtn) {
            floatBtn.addEventListener('click', function () {
                enableAnim();
                applyDesktop(false);
                persist(false);
            });
        }

        /* 移动端汉堡按钮：打开抽屉 */
        if (toggleBtn) {
            toggleBtn.addEventListener('click', function () {
                if (isCollapsed()) { /* 移动端 collapsed 由媒体查询控制，这里只关心 open */ }
                openMobile();
            });
        }

        /* 移动端遮罩：点击关闭 */
        if (backdrop) {
            backdrop.addEventListener('click', closeMobile);
        }

        /* 子菜单分组：点击 .app-nav-toggle 展开/收起所在 .app-nav-sub。
         * 展开态由 CSS 的 .app-nav-sub.open 控制（子菜单 max-height 0→320px）。
         * 用 localStorage 记住各分组的展开状态，刷新/切页后保持不变，解决"每次都自动收起"；
         * 不做互斥，允许多个分组同时展开，便于跨组跳转。 */
        var GROUP_KEY = 'appSidebarOpenGroups'; // 已展开分组的标识数组

        /* 读取已保存的展开分组集合（异常时返回空集合） */
        function readOpenGroups() {
            try {
                var raw = localStorage.getItem(GROUP_KEY);
                var arr = raw ? JSON.parse(raw) : [];
                return Array.isArray(arr) ? arr : [];
            } catch (e) { return []; }
        }

        /* 保存当前展开分组集合；集合为空时移除记录，避免残留无用数据 */
        function writeOpenGroups(groups) {
            try { localStorage.setItem(GROUP_KEY, JSON.stringify(groups)); } catch (e) { /* 隐私模式忽略 */ }
        }

        /* 为每个分组生成稳定标识：优先用首个链接文案，回退到序号 */
        function groupId(sub, index) {
            var label = sub.querySelector('.app-nav-toggle span');
            return (label && label.textContent.trim()) || ('group-' + index);
        }

        var openGroups = readOpenGroups();

        document.querySelectorAll('.app-nav-sub').forEach(function (sub, index) {
            var toggle = sub.querySelector('.app-nav-toggle');
            if (!toggle) { return; }

            var id = groupId(sub, index);

            if (openGroups.indexOf(id) !== -1) {
                sub.classList.add('open'); // 首屏恢复上次的展开状态
            }

            /* 当前页面属于该分组时给父级打标：折叠成图标栏后子项不可见，
             * 用父级图标高亮提示用户"当前页在这个分组里" */
            if (sub.querySelector('.app-nav-sub-link.active')) {
                sub.classList.add('has-active');
            }

            toggle.addEventListener('click', function (e) {
                e.preventDefault(); // 阻止 href="#" 跳转到页首

                /* 折叠图标栏状态下点击分组图标：先展开侧栏，再展开该分组，
                 * 避免"点了没反应"（子菜单在图标栏中不可见） */
                if (!isMobile() && isCollapsed()) {
                    enableAnim();
                    applyDesktop(false);
                    persist(false);
                    sub.classList.add('open');
                    if (openGroups.indexOf(id) === -1) { openGroups.push(id); }
                    writeOpenGroups(openGroups);
                    return;
                }

                var isOpen = sub.classList.toggle('open');
                var idx = openGroups.indexOf(id);
                if (isOpen) {
                    if (idx === -1) { openGroups.push(id); } // 记录为展开
                } else if (idx !== -1) {
                    openGroups.splice(idx, 1);               // 记录为收起
                }
                writeOpenGroups(openGroups);
            });
        });

        /* 顶部分组（如"资材与文件"）：点击分组标题展开/收起该组全部二级页面。
         * 展开状态用 localStorage 记住，刷新/切页后保持；当前页所在分组自动展开，
         * 不做互斥，允许多个分组同时展开。 */
        var SECTION_KEY = 'appSidebarOpenSections';

        function readOpenSections() {
            try {
                var raw = localStorage.getItem(SECTION_KEY);
                var arr = raw ? JSON.parse(raw) : [];
                return Array.isArray(arr) ? arr : [];
            } catch (e) { return []; }
        }

        function writeOpenSections(sections) {
            try { localStorage.setItem(SECTION_KEY, JSON.stringify(sections)); } catch (e) { /* 隐私模式忽略 */ }
        }

        var openSections = readOpenSections();
        var currentPath = window.location.pathname;

        document.querySelectorAll('.app-nav-section').forEach(function (section, index) {
            var toggle = section.querySelector('.app-nav-group-toggle');
            if (!toggle) { return; }
            var id = section.getAttribute('data-group') || ('section-' + index);

            /* 恢复上次展开的分组 */
            if (openSections.indexOf(id) !== -1) {
                section.classList.add('open');
                toggle.setAttribute('aria-expanded', 'true');
            }

            /* 当前页所在分组自动展开：优先看 active 标记，否则按链接地址匹配 */
            var activeLink = section.querySelector('.app-nav-link.active');
            if (!activeLink) {
                section.querySelectorAll('a.app-nav-link[href]').forEach(function (link) {
                    if (!activeLink && link.pathname === currentPath) { activeLink = link; }
                });
            }
            if (activeLink) {
                section.classList.add('open');
                toggle.setAttribute('aria-expanded', 'true');
            }

            toggle.addEventListener('click', function () {
                enableAnim();
                /* 折叠图标栏下分组标题不可见，此处无需处理 collapsed 场景 */
                var isOpen = section.classList.toggle('open');
                toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
                var idx = openSections.indexOf(id);
                if (isOpen) {
                    if (idx === -1) { openSections.push(id); }
                } else if (idx !== -1) {
                    openSections.splice(idx, 1);
                }
                writeOpenSections(openSections);
            });
        });

        /* Alt+S 快捷键：桌面折叠/展开 */
        document.addEventListener('keydown', function (e) {
            if (e.altKey && (e.key === 's' || e.key === 'S')) {
                e.preventDefault();
                enableAnim();
                var next = !isCollapsed();
                applyDesktop(next);
                persist(next);
            }
        });

        /* 视口切换（跨断点）时同步状态，避免桌面折叠态残留到移动端 */
        window.addEventListener('resize', function () {
            if (isMobile()) {
                // 进入移动端：抽屉改为媒体查询控制，仅收起遮罩与抽屉，不动首屏同步类
                body.classList.remove('app-sidebar-open');
                body.classList.remove('app-sidebar-collapsed');
                sidebar.classList.remove('open');
                /* 清除桌面折叠痕迹，避免移动端抽屉被 collapsed-init 压回屏幕外 */
                sidebar.classList.remove('collapsed');
                root.classList.remove('app-sidebar-collapsed-init');
                if (backdrop) { backdrop.classList.remove('show'); }
                if (floatBtn) { floatBtn.classList.remove('show'); }
            } else {
                // 回到桌面端：按持久化状态恢复
                var collapsed = false;
                try { collapsed = localStorage.getItem(STORAGE_KEY) === '1'; } catch (e) { /* 忽略 */ }
                closeMobile();
                applyDesktop(collapsed);
            }
        });
    });
})();
