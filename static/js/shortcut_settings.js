/**
 * 快捷键设置页：自定义网址（固定入口 → 可调整目标）
 * 数据接口：
 *   GET  /api/web_shortcuts          读取自定义网址清单
 *   POST /api/web_shortcuts          新增或更新（携带 id 即更新）
 *   POST /api/web_shortcuts/delete   按 id 删除
 * 说明：所有用户输入统一用 textContent 渲染，避免 XSS。
 */
(function () {
    'use strict';

    var API = '/api/web_shortcuts';
    var items = [];                       // 当前网址清单
    var modal = null;                     // Bootstrap 弹窗实例
    var statusTimer = null;

    var bodyEl = document.getElementById('scBody');
    var statusEl = document.getElementById('scStatus');
    var formEl = document.getElementById('scForm');
    var saveBtn = document.getElementById('scSaveBtn');

    /** 统一状态提示，3 秒后自动清除 */
    function setStatus(message, isError) {
        statusEl.textContent = message || '';
        statusEl.className = 'sc-status' + (isError ? ' err' : ' ok');
        clearTimeout(statusTimer);
        if (message) {
            statusTimer = setTimeout(function () { statusEl.textContent = ''; }, 3000);
        }
    }

    /** 统一请求封装：解析 JSON，网络异常时给出可读提示 */
    function request(url, options) {
        return fetch(url, Object.assign({ credentials: 'same-origin' }, options || {}))
            .then(function (res) {
                return res.json().catch(function () {
                    return { success: false, error: '服务返回格式异常' };
                });
            });
    }

    /** 固定入口完整地址（封装 APK 时使用） */
    function fullUrl(alias) {
        return window.location.origin + (alias || '');
    }

    /** 生成操作按钮 */
    function actionButton(text, className, onClick) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn ' + className;
        btn.textContent = text;
        btn.addEventListener('click', onClick);
        return btn;
    }

    /** 复制文本：优先 Clipboard API，HTTP 环境下回退到临时文本框 */
    function copyText(text) {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(text);
        }
        return new Promise(function (resolve, reject) {
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            try {
                document.execCommand('copy') ? resolve() : reject(new Error('copy failed'));
            } catch (e) {
                reject(e);
            } finally {
                document.body.removeChild(ta);
            }
        });
    }

    /** 渲染单行 */
    function renderRow(item, index) {
        var tr = document.createElement('tr');

        var idx = document.createElement('td');
        idx.className = 'sc-index';
        idx.textContent = String(index + 1);
        tr.appendChild(idx);

        var aliasTd = document.createElement('td');
        if (item.alias) {
            var aliasLink = document.createElement('a');
            aliasLink.className = 'sc-url';
            aliasLink.href = fullUrl(item.alias);
            aliasLink.target = '_blank';
            aliasLink.rel = 'noopener noreferrer';
            aliasLink.title = fullUrl(item.alias);
            aliasLink.textContent = fullUrl(item.alias);
            aliasTd.appendChild(aliasLink);
        } else {
            var warn = document.createElement('span');
            warn.className = 'sc-remark';
            warn.textContent = '未设置（请编辑补全）';
            aliasTd.appendChild(warn);
        }
        tr.appendChild(aliasTd);

        var targetTd = document.createElement('td');
        if (item.target) {
            var targetLink = document.createElement('a');
            targetLink.className = 'sc-url';
            targetLink.style.color = '#475569';
            targetLink.href = item.target;
            targetLink.target = '_blank';
            targetLink.rel = 'noopener noreferrer';
            targetLink.title = item.target;
            targetLink.textContent = item.target;
            targetTd.appendChild(targetLink);
        }
        tr.appendChild(targetTd);

        var remark = document.createElement('td');
        remark.className = 'sc-remark sc-hide-sm';
        remark.textContent = item.remark || '';
        tr.appendChild(remark);

        var updated = document.createElement('td');
        updated.className = 'sc-updated sc-hide-sm';
        updated.textContent = item.updated_at || item.created_at || '';
        tr.appendChild(updated);

        var actions = document.createElement('td');
        var wrap = document.createElement('div');
        wrap.className = 'sc-actions';
        wrap.appendChild(actionButton('复制', 'btn-outline-primary', function () {
            copyText(fullUrl(item.alias)).then(function () {
                setStatus('已复制固定入口地址', false);
            }).catch(function () {
                setStatus('复制失败，请手动选择地址复制', true);
            });
        }));
        wrap.appendChild(actionButton('编辑', 'btn-outline-secondary', function () { openEdit(item); }));
        wrap.appendChild(actionButton('删除', 'btn-outline-danger', function () { removeItem(item); }));
        actions.appendChild(wrap);
        tr.appendChild(actions);
        return tr;
    }

    /** 全量渲染清单 */
    function render() {
        bodyEl.textContent = '';
        if (!items.length) {
            var emptyRow = document.createElement('tr');
            var cell = document.createElement('td');
            cell.colSpan = 6;
            cell.className = 'sc-empty';
            cell.textContent = '暂无自定义网址，点击「新增网址」开始配置';
            emptyRow.appendChild(cell);
            bodyEl.appendChild(emptyRow);
            return;
        }
        var frag = document.createDocumentFragment();
        items.forEach(function (item, index) { frag.appendChild(renderRow(item, index)); });
        bodyEl.appendChild(frag);
    }

    /** 读取清单 */
    function load() {
        request(API).then(function (data) {
            if (!data.success) {
                setStatus(data.error || '读取清单失败', true);
                return;
            }
            items = data.items || [];
            render();
        }).catch(function () {
            setStatus('网络异常，无法读取清单', true);
        });
    }

    /** 打开新增弹窗 */
    function openAdd() {
        document.getElementById('scModalTitle').textContent = '新增网址';
        document.getElementById('scId').value = '';
        formEl.reset();
        modal.show();
    }

    /** 打开编辑弹窗，预填当前值 */
    function openEdit(item) {
        document.getElementById('scModalTitle').textContent = '编辑网址';
        document.getElementById('scId').value = item.id;
        document.getElementById('scAlias').value = item.alias || '';
        document.getElementById('scTarget').value = item.target || '';
        document.getElementById('scRemark').value = item.remark || '';
        modal.show();
    }

    /** 提交新增/编辑 */
    function submitForm(event) {
        event.preventDefault();
        var payload = {
            id: document.getElementById('scId').value,
            alias: document.getElementById('scAlias').value.trim(),
            target: document.getElementById('scTarget').value.trim(),
            remark: document.getElementById('scRemark').value.trim()
        };
        if (!payload.alias) { setStatus('请输入自定义网址（固定入口）', true); return; }
        if (!payload.target) { setStatus('请输入目标网址', true); return; }

        saveBtn.disabled = true;
        request(API, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(function (data) {
            if (!data.success) {
                setStatus(data.error || '保存失败', true);
                return;
            }
            items = data.items || [];
            render();
            modal.hide();
            setStatus(data.message || '保存成功', false);
        }).catch(function () {
            setStatus('网络异常，保存失败', true);
        }).finally(function () {
            saveBtn.disabled = false;
        });
    }

    /** 删除单条记录 */
    function removeItem(item) {
        if (!window.confirm('确定删除自定义网址「' + (item.alias || '') + '」吗？')) { return; }
        request(API + '/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: item.id })
        }).then(function (data) {
            if (!data.success) {
                setStatus(data.error || '删除失败', true);
                return;
            }
            items = data.items || [];
            render();
            setStatus(data.message || '删除成功', false);
        }).catch(function () {
            setStatus('网络异常，删除失败', true);
        });
    }

    document.getElementById('scAddBtn').addEventListener('click', openAdd);
    formEl.addEventListener('submit', submitForm);
    modal = new bootstrap.Modal(document.getElementById('scModal'));

    load();
})();
