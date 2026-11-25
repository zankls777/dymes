/**
 * 侧边栏分组管理（菜单管理页）
 * 数据来源：GET /api/sidebar_menu → { layout, catalog }
 * 保存方式：POST /api/sidebar_menu，仅提交 { groups: [{key, title, entries: []}] }
 * 交互：分组卡片、菜单条目均通过拖拽调整顺序与归属（HTML5 Drag & Drop，无外部依赖）
 */
(function () {
    'use strict';

    var API_BASE = '/api/sidebar_menu';
    var catalog = {};                 // 条目 key -> {title, icon, kind, summary}
    var layout = { groups: [] };      // 当前编辑中的分组布局
    var drag = null;                  // 拖拽中数据：{type:'group'|'entry', groupIndex, entryIndex}

    var groupsEl = document.getElementById('mmGroups');
    var statusEl = document.getElementById('mmStatus');
    var saveBtn = document.getElementById('mmSave');
    var resetBtn = document.getElementById('mmReset');
    var addBtn = document.getElementById('mmAddGroup');

    /** 统一提示：成功为绿色，失败为红色 */
    function setStatus(message, isError) {
        statusEl.textContent = message || '';
        statusEl.className = 'mm-status' + (isError ? ' err' : ' ok');
    }

    function request(url, options) {
        var opts = Object.assign({ credentials: 'same-origin' }, options || {});
        return fetch(url, opts).then(function (res) {
            return res.json().catch(function () {
                return { success: false, error: '服务返回格式异常' };
            });
        });
    }

    /** 清理所有拖拽高亮，避免残留样式 */
    function clearDropHints() {
        document.querySelectorAll('.mm-group.drag-over, .mm-entries.drop-into, .mm-entry.drop-before, .mm-entry.drop-after, .dragging')
            .forEach(function (el) {
                el.classList.remove('drag-over', 'drop-into', 'drop-before', 'drop-after', 'dragging');
            });
    }

    /** 生成小图标按钮（删除分组等） */
    function iconButton(icon, title, disabled, danger, onClick) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'mm-icon-btn' + (danger ? ' danger' : '');
        btn.title = title;
        btn.setAttribute('aria-label', title);
        btn.disabled = !!disabled;
        var i = document.createElement('i');
        i.className = 'fa ' + icon;
        btn.appendChild(i);
        btn.addEventListener('click', onClick);
        return btn;
    }

    /** 分组卡片：拖动手柄排序、改名、删除；菜单可在卡片间拖拽 */
    function renderGroup(group, index) {
        var card = document.createElement('div');
        card.className = 'mm-group';
        card.dataset.key = group.key;

        var head = document.createElement('div');
        head.className = 'mm-group-head';

        // 拖动手柄：仅通过手柄拖动整个分组，避免与输入框选择文字冲突
        var grip = document.createElement('i');
        grip.className = 'fa fa-bars mm-grip';
        grip.title = '拖动调整分组顺序';
        grip.draggable = true;
        grip.addEventListener('dragstart', function (e) {
            drag = { type: 'group', groupIndex: index };
            card.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', 'group');
        });
        grip.addEventListener('dragend', function () {
            drag = null;
            clearDropHints();
        });

        var input = document.createElement('input');
        input.className = 'mm-title-input';
        input.value = group.title || '';
        input.maxLength = 20;
        input.setAttribute('aria-label', '分组名称');
        input.addEventListener('change', function () {
            group.title = input.value.trim() || '未命名分组';
            input.value = group.title;
        });

        var count = document.createElement('span');
        count.className = 'mm-count';
        count.textContent = group.entries.length + ' 项';

        var actions = document.createElement('div');
        actions.className = 'd-flex';
        actions.style.gap = '4px';
        actions.appendChild(iconButton('fa-trash', '删除分组', layout.groups.length <= 1, true, function () {
            removeGroup(index);
        }));

        head.appendChild(grip);
        head.appendChild(input);
        head.appendChild(count);
        head.appendChild(actions);

        var entries = document.createElement('div');
        entries.className = 'mm-entries';

        // 菜单拖入空白区域：追加到本分组末尾
        entries.addEventListener('dragover', function (e) {
            if (!drag || drag.type !== 'entry') { return; }
            e.preventDefault();
            e.stopPropagation();
            entries.classList.add('drop-into');
        });
        entries.addEventListener('dragleave', function (e) {
            if (!entries.contains(e.relatedTarget)) { entries.classList.remove('drop-into'); }
        });
        entries.addEventListener('drop', function (e) {
            if (!drag || drag.type !== 'entry') { return; }
            e.preventDefault();
            e.stopPropagation();
            entries.classList.remove('drop-into');
            moveEntry(drag.groupIndex, drag.entryIndex, index, layout.groups[index].entries.length);
        });

        if (group.entries.length) {
            group.entries.forEach(function (entryKey, entryIndex) {
                entries.appendChild(renderEntry(entryKey, index, entryIndex));
            });
        } else {
            var empty = document.createElement('div');
            empty.className = 'mm-empty';
            empty.textContent = '暂无菜单，把菜单拖到这里';
            entries.appendChild(empty);
        }

        // 分组整体作为拖放目标：拖动分组时调整分组顺序
        card.addEventListener('dragover', function (e) {
            if (!drag || drag.type !== 'group') { return; }
            e.preventDefault();
            card.classList.add('drag-over');
        });
        card.addEventListener('dragleave', function (e) {
            if (!card.contains(e.relatedTarget)) { card.classList.remove('drag-over'); }
        });
        card.addEventListener('drop', function (e) {
            if (!drag || drag.type !== 'group') { return; }
            e.preventDefault();
            e.stopPropagation();
            card.classList.remove('drag-over');
            // 按卡片中线判断插入到目标分组之前/之后
            var rect = card.getBoundingClientRect();
            var after = (e.clientY - rect.top) > rect.height / 2;
            moveGroup(drag.groupIndex, index + (after ? 1 : 0));
        });

        card.appendChild(head);
        card.appendChild(entries);
        return card;
    }

    /** 单个菜单条目：整行可拖动，同组排序或跨组移动 */
    function renderEntry(entryKey, groupIndex, entryIndex) {
        var meta = catalog[entryKey] || { title: entryKey, icon: 'fa-circle-o', summary: '' };

        var row = document.createElement('div');
        row.className = 'mm-entry';
        row.draggable = true;
        row.dataset.key = entryKey;

        row.addEventListener('dragstart', function (e) {
            drag = { type: 'entry', groupIndex: groupIndex, entryIndex: entryIndex };
            row.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', 'entry');
        });
        row.addEventListener('dragend', function () {
            drag = null;
            clearDropHints();
        });

        // 拖到条目上：按鼠标位置判断插入到前半（前）还是后半（后）
        row.addEventListener('dragover', function (e) {
            if (!drag || drag.type !== 'entry') { return; }
            e.preventDefault();
            e.stopPropagation();
            var rect = row.getBoundingClientRect();
            var after = (e.clientY - rect.top) > rect.height / 2;
            row.classList.toggle('drop-after', after);
            row.classList.toggle('drop-before', !after);
        });
        row.addEventListener('dragleave', function () {
            row.classList.remove('drop-before', 'drop-after');
        });
        row.addEventListener('drop', function (e) {
            if (!drag || drag.type !== 'entry') { return; }
            e.preventDefault();
            e.stopPropagation();
            var rect = row.getBoundingClientRect();
            var after = (e.clientY - rect.top) > rect.height / 2;
            var insertIndex = entryIndex + (after ? 1 : 0);
            row.classList.remove('drop-before', 'drop-after');
            moveEntry(drag.groupIndex, drag.entryIndex, groupIndex, insertIndex);
        });

        var icon = document.createElement('i');
        icon.className = 'fa ' + meta.icon;

        var title = document.createElement('span');
        title.className = 'mm-entry-title';
        title.textContent = meta.title;

        var summary = document.createElement('span');
        summary.className = 'mm-entry-summary';
        summary.textContent = meta.summary || '';
        summary.title = meta.summary || '';

        row.appendChild(icon);
        row.appendChild(title);
        row.appendChild(summary);
        return row;
    }

    function render() {
        groupsEl.innerHTML = '';
        if (!layout.groups.length) {
            groupsEl.innerHTML = '<div class="mm-empty">暂无分组，请先新增分组</div>';
            return;
        }
        layout.groups.forEach(function (group, index) {
            groupsEl.appendChild(renderGroup(group, index));
        });
    }

    /** 分组排序：to 为目标插入位置（0 ~ groups.length） */
    function moveGroup(from, to) {
        if (from === to) { return; }
        var moved = layout.groups.splice(from, 1)[0];
        if (from < to) { to -= 1; }
        layout.groups.splice(to, 0, moved);
        render();
        setStatus('已调整分组顺序，请保存后生效');
    }

    /** 菜单排序/跨组：toIndex 为目标分组内的插入位置 */
    function moveEntry(fromGroup, fromIndex, toGroup, toIndex) {
        if (fromGroup === toGroup && (toIndex === fromIndex || toIndex === fromIndex + 1)) { return; }
        var moved = layout.groups[fromGroup].entries.splice(fromIndex, 1)[0];
        if (fromGroup === toGroup && fromIndex < toIndex) { toIndex -= 1; }
        layout.groups[toGroup].entries.splice(toIndex, 0, moved);
        render();
        setStatus('已调整菜单归属/顺序，请保存后生效');
    }

    function removeGroup(index) {
        if (layout.groups.length <= 1) { return; }
        var target = layout.groups[index];
        if (!window.confirm('删除分组“' + (target.title || '未命名分组') + '”？其中菜单将合并到相邻分组。')) { return; }
        var receiver = index === 0 ? layout.groups[1] : layout.groups[index - 1];
        receiver.entries = receiver.entries.concat(target.entries);
        layout.groups.splice(index, 1);
        render();
        setStatus('已删除分组，菜单已合并到“' + receiver.title + '”，请保存后生效');
    }

    function addGroup() {
        var now = new Date();
        var key = 'g' + now.getTime();
        layout.groups.push({ key: key, title: '新分组', entries: [] });
        render();
        setStatus('已新增分组，请修改名称并保存');
    }

    function save() {
        saveBtn.disabled = true;
        setStatus('正在保存…', false);
        request(API_BASE, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ layout: layout })
        }).then(function (res) {
            saveBtn.disabled = false;
            if (!res.success) {
                setStatus(res.error || '保存失败', true);
                return;
            }
            layout = res.layout;
            render();
            setStatus(res.message || '配置已保存，刷新页面即可看到最新分组');
        }).catch(function () {
            saveBtn.disabled = false;
            setStatus('网络异常，保存失败', true);
        });
    }

    function reset() {
        if (!window.confirm('恢复默认分组会覆盖当前配置，是否继续？')) { return; }
        resetBtn.disabled = true;
        request(API_BASE + '/reset', { method: 'POST' }).then(function (res) {
            resetBtn.disabled = false;
            if (!res.success) {
                setStatus(res.error || '恢复失败', true);
                return;
            }
            layout = res.layout;
            render();
            setStatus(res.message || '已恢复默认分组');
        }).catch(function () {
            resetBtn.disabled = false;
            setStatus('网络异常，恢复失败', true);
        });
    }

    function load() {
        request(API_BASE, { method: 'GET' }).then(function (res) {
            if (!res.success) {
                groupsEl.innerHTML = '';
                setStatus(res.error || '加载失败', true);
                return;
            }
            catalog = {};
            (res.catalog && res.catalog.entries || []).forEach(function (item) {
                catalog[item.key] = item;
            });
            layout = res.layout || { groups: [] };
            render();
        }).catch(function () {
            groupsEl.innerHTML = '';
            setStatus('网络异常，加载失败', true);
        });
    }

    // 拖动分组到卡片之间的空白区域：移动到末尾
    groupsEl.addEventListener('dragover', function (e) {
        if (drag && drag.type === 'group') { e.preventDefault(); }
    });
    groupsEl.addEventListener('drop', function (e) {
        if (!drag || drag.type !== 'group') { return; }
        e.preventDefault();
        moveGroup(drag.groupIndex, layout.groups.length);
    });

    addBtn.addEventListener('click', addGroup);
    saveBtn.addEventListener('click', save);
    resetBtn.addEventListener('click', reset);
    load();
})();
