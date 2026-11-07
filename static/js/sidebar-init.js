/* 首屏同步脚本：在样式渲染前落位「整栏折叠状态」与「分组展开状态」，消除切换模块时的闪烁。
 * 本脚本位于侧边栏 DOM 之后、CSS 之后同步执行，此时分组元素已存在，可一次性落位。 */
(function () {
    var root = document.documentElement;

    try {
        /* 标记首屏同步阶段：CSS 借此关闭子菜单过渡，避免首屏展开分组时出现"滑下"动画 */
        root.classList.add('app-sidebar-nav-init');

        /* 1) 整栏折叠状态：仅桌面端生效。
         * 移动端侧栏是抽屉式，不参与"桌面折叠"，否则 localStorage 里桌面留下的
         * collapsed 标记会在移动端挂上 app-sidebar-collapsed-init（高优先级），
         * 表现为"首次能展开、之后每次刷新都展不开"。 */
        var isMobile = window.innerWidth < 768;
        var saved = window.localStorage.getItem('appSidebarCollapsed');
        var collapsed = !isMobile && saved === '1'; // 移动端强制按展开处理
        if (collapsed) {
            root.classList.add('app-sidebar-collapsed-init');
            /* 同步给侧栏加 collapsed：折叠态是 80px 图标栏，
             * 仅靠 html 类只能改变宽度，隐藏文字/子菜单等规则依赖 .app-sidebar.collapsed */
            var sidebar = document.getElementById('appSidebar');
            if (sidebar) { sidebar.classList.add('collapsed'); }
        } else if (!isMobile) {
            root.classList.add('app-sidebar-open-init');
        }

        /* 2) 分组展开状态：读取上次记住的展开分组，首屏直接落位。
         * 与 sidebar.js 的 GROUP_KEY / groupId 逻辑保持一致，避免首屏收起再展开的跳动。 */
        var raw = window.localStorage.getItem('appSidebarOpenGroups');
        var openGroups = [];
        try { openGroups = raw ? JSON.parse(raw) : []; } catch (e2) { openGroups = []; }
        if (!Array.isArray(openGroups)) { openGroups = []; }

        document.querySelectorAll('.app-nav-sub').forEach(function (sub, index) {
            var label = sub.querySelector('.app-nav-toggle span');
            var id = (label && label.textContent.trim()) || ('group-' + index);
            /* 用户曾展开的分组：首屏直接展开 */
            if (openGroups.indexOf(id) !== -1) {
                sub.classList.add('open');
            }
        });
    } catch (e) { /* 忽略隐私模式等异常 */ }
})();
