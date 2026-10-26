function highlightDifferences() {
    // 获取表格元素
    const table = document.querySelector('table');
    if (!table) return;

    // 获取表头和所有行
    const headers = Array.from(table.querySelectorAll('th'));
    const rows = Array.from(table.querySelectorAll('tbody tr'));

    // 跳过处理，如果没有足够的行
    if (rows.length < 2) return;

    // 为每列创建一个映射，存储值和对应的颜色
    const columnValueColors = {};

    // 遍历每一列
    headers.forEach((header, colIndex) => {
        // 跳过不需要比较的列（如ID列）
        if (header.textContent.trim() === 'ID' ||
            header.textContent.trim() === '操作' ||
            header.textContent.trim() === '上传时间') {
            return;
        }

        // 收集该列的所有唯一值
        const values = new Set();
        rows.forEach(row => {
            const cell = row.cells[colIndex];
            if (cell) {
                values.add(cell.textContent.trim());
            }
        });

        // 如果该列只有一个值，不需要高亮
        if (values.size <= 1) return;

        // 为该列的每个唯一值分配一个颜色
        const valueColors = {};
            const colors = [
        '#ffdddd', '#ddffdd', '#ddddff', '#ffffdd', '#ffddff', '#ddffff', // 原始颜色
        '#ffe6cc', '#ccffcc', '#cce6ff', '#ffffcc', '#ffccff', '#ccffff', // 更淡的颜色
        '#ffcccc', '#ccffcc', '#ccccff', '#ffffcc', '#ffccff', '#ccffff', // 中等深浅的颜色
        '#ffb3b3', '#b3ffb3', '#b3b3ff', '#ffffb3', '#ffb3ff', '#b3ffff', // 更深的颜色
        '#ff9999', '#99ff99', '#9999ff', '#ffff99', '#ff99ff', '#99ffff'  // 最深的颜色
    ];

        let colorIndex = 0;
        values.forEach(value => {
            valueColors[value] = colors[colorIndex % colors.length];
            colorIndex++;
        });

        // 保存该列的值-颜色映射
        columnValueColors[colIndex] = valueColors;
    });

    // 应用颜色到单元格
    rows.forEach(row => {
        Object.entries(columnValueColors).forEach(([colIndex, valueColors]) => {
            const cell = row.cells[colIndex];
            if (cell) {
                const value = cell.textContent.trim();
                if (valueColors[value]) {
                    cell.style.backgroundColor = valueColors[value];
                }
            }
        });
    });

    // 更新按钮状态
    const showDifferencesBtn = document.getElementById('show-differences-btn');
    if (showDifferencesBtn) {
        showDifferencesBtn.textContent = '隐藏差异';
        showDifferencesBtn.className = 'btn btn-warning';
        showDifferencesBtn.dataset.differencesVisible = 'true';
    }
}

function hideDifferences() {
    // 移除所有单元格的背景色
    const cells = document.querySelectorAll('table td');
    cells.forEach(cell => {
        cell.style.backgroundColor = '';
    });

    // 更新按钮状态
    const showDifferencesBtn = document.getElementById('show-differences-btn');
    if (showDifferencesBtn) {
        showDifferencesBtn.textContent = '显示差异';
        showDifferencesBtn.className = 'btn btn-warning';
        showDifferencesBtn.dataset.differencesVisible = 'false';
    }
}

// 检查并应用差异高亮状态
function checkAndApplyDifferencesState() {
    // 从localStorage获取差异显示状态
    const showDifferences = localStorage.getItem('showSubListDifferences') === 'true';

    // 从按钮数据属性获取状态（用于页面内状态切换）
    const showDifferencesBtn = document.getElementById('show-differences-btn');
    const buttonState = showDifferencesBtn ? showDifferencesBtn.dataset.differencesVisible === 'true' : false;

    // 使用按钮状态优先，如果没有按钮状态则使用localStorage状态
    const shouldShowDifferences = buttonState || showDifferences;

    if (shouldShowDifferences) {
        highlightDifferences();
    } else {
        hideDifferences();
    }
}

// 页面加载完成后执行
document.addEventListener('DOMContentLoaded', () => {
    const showDifferencesBtn = document.getElementById('show-differences-btn');
    if (showDifferencesBtn) {
        // 为显示差异按钮添加点击事件
        showDifferencesBtn.addEventListener('click', () => {
            const isVisible = showDifferencesBtn.dataset.differencesVisible === 'true';

            if (isVisible) {
                hideDifferences();
                localStorage.setItem('showSubListDifferences', 'false');
            } else {
                highlightDifferences();
                localStorage.setItem('showSubListDifferences', 'true');
            }
        });
    }

    // 检查并应用差异显示状态
    checkAndApplyDifferencesState();

    // 为分页链接添加事件监听
    const paginationLinks = document.querySelectorAll('.pagination a');
    paginationLinks.forEach(link => {
        link.addEventListener('click', () => {
            // 保存当前差异显示状态到localStorage
            const showDifferencesBtn = document.getElementById('show-differences-btn');
            if (showDifferencesBtn) {
                localStorage.setItem('showSubListDifferences', showDifferencesBtn.dataset.differencesVisible === 'true');
            }
        });
    });

    // 为每页显示选择框添加事件监听
    const perPageSelect = document.querySelector('select[onchange="changePerPage(this.value)"]');
    if (perPageSelect) {
        perPageSelect.addEventListener('change', () => {
            // 保存当前差异显示状态到localStorage
            const showDifferencesBtn = document.getElementById('show-differences-btn');
            if (showDifferencesBtn) {
                localStorage.setItem('showSubListDifferences', showDifferencesBtn.dataset.differencesVisible === 'true');
            }
        });
    }
});

// 为changePerPage函数添加事件监听
window.changePerPage = function(perPage) {
    // 保存当前差异显示状态到localStorage
    const showDifferencesBtn = document.getElementById('show-differences-btn');
    if (showDifferencesBtn) {
        localStorage.setItem('showSubListDifferences', showDifferencesBtn.dataset.differencesVisible === 'true');
    }

    // 原始的changePerPage逻辑
    const currentPage = 1; // 重置到第一页
    const url = new URL(window.location.href);
    url.searchParams.set('page', currentPage);
    url.searchParams.set('per_page', perPage);

    // 获取并保留其他搜索参数
    const searchParams = document.querySelectorAll('input[type="text"]');
    searchParams.forEach(input => {
        if (input.value) {
            url.searchParams.set(input.name, input.value);
        }
    });

    window.location.href = url.toString();
};