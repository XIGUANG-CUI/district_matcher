/* 区划匹配页面脚本（外链，CSP 友好；不使用内联事件） */
(function () {
    'use strict';

    var app = document.getElementById('dm-app');
    if (!app) return;

    var URLS = {
        query: app.dataset.queryUrl,
        preview: app.dataset.previewUrl,
        batch: app.dataset.batchUrl,
        progress: app.dataset.progressUrl,
        batches: app.dataset.batchesUrl,
        results: app.dataset.resultsUrl,
        exportUrl: app.dataset.exportUrl,
        deleteUrl: app.dataset.deleteUrl,
        dictionary: app.dataset.dictionaryUrl,
        dictImport: app.dataset.dictImportUrl,
        history: app.dataset.historyUrl,
        backfill: app.dataset.backfillUrl
    };

    var $ = function (id) { return document.getElementById(id); };
    var statusClass = {
        '正确': 'success',
        '已解析': 'primary',
        '已解析(仅证件)': 'info',
        '需复核': 'warning',
        '部分': 'warning',
        '无匹配': 'secondary',
        '异常': 'danger'
    };

    function withId(template, id) {
        return template.replace('/0/', '/' + id + '/');
    }

    function requestJSON(url, options) {
        return fetch(url, options).then(function (resp) {
            return safeJSON(resp).then(function (body) {
                if (!resp.ok || body.success === false) {
                    var msg = (body && body.message) || ('请求失败（HTTP ' + resp.status + '）');
                    throw new Error(msg);
                }
                return body;
            });
        });
    }

    function cell(text) {
        var td = document.createElement('td');
        td.textContent = text === null || text === undefined ? '' : String(text);
        return td;
    }

    function badge(status) {
        var span = document.createElement('span');
        span.className = 'badge text-bg-' + (statusClass[status] || 'secondary');
        span.textContent = status || '未计算';
        return span;
    }

    function setBusy(button, busy, text) {
        if (!button) return;
        button.disabled = busy;
        if (busy) {
            button.dataset.original = button.textContent;
            button.textContent = text || '处理中…';
        } else if (button.dataset.original) {
            button.textContent = button.dataset.original;
        }
    }

    /* ---------------------------------------------------------- 1. 单条查询 */
    $('dm-query-form').addEventListener('submit', function (event) {
        event.preventDefault();
        var idCard = $('dm-id-card').value.trim();
        var address = $('dm-address').value.trim();
        if (!idCard && !address) {
            showToast('请输入身份证号或地址', 'warning');
            return;
        }
        var btn = $('dm-query-btn');
        setBusy(btn, true, '查询中…');
        requestJSON(URLS.query, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id_card: idCard, address: address })
        }).then(function (body) {
            var data = body.data || {};
            var fields = $('dm-query-fields').querySelectorAll('[data-key]');
            Array.prototype.forEach.call(fields, function (dd) {
                dd.textContent = data[dd.dataset.key] || '-';
            });
            var summary = $('dm-query-summary');
            summary.textContent = '匹配状态：' + (data.match_status || '-') +
                '　说明：' + (data.decision_path || '-');
            $('dm-detail').textContent =
                '【引擎①】\n' + (data.engine1_detail || '-') +
                '\n\n【引擎②】\n' + (data.engine2_detail || '-');
        }).catch(function (err) {
            showToast(err.message, 'danger');
        }).finally(function () {
            setBusy(btn, false);
        });
    });

    /* ---------------------------------------------------------- 2. 批量匹配 */
    var headers = [];

    function fillSelect(select, list, selected) {
        select.innerHTML = '';
        var empty = document.createElement('option');
        empty.value = '';
        empty.textContent = '（不选择）';
        select.appendChild(empty);
        list.forEach(function (name) {
            var option = document.createElement('option');
            option.value = name;
            option.textContent = name;
            if (name === selected) option.selected = true;
            select.appendChild(option);
        });
    }

    $('dm-source-upload').addEventListener('change', toggleSource);
    $('dm-source-students').addEventListener('change', toggleSource);

    function toggleSource() {
        var isUpload = $('dm-source-upload').checked;
        $('dm-upload-panel').classList.toggle('d-none', !isUpload);
        $('dm-students-panel').classList.toggle('d-none', isUpload);
    }

    $('dm-preview-btn').addEventListener('click', function () {
        var file = $('dm-file').files[0];
        if (!file) {
            showToast('请先选择文件', 'warning');
            return;
        }
        var form = new FormData();
        form.append('file', file);
        var btn = $('dm-preview-btn');
        setBusy(btn, true, '解析中…');
        requestJSON(URLS.preview, { method: 'POST', body: form }).then(function (body) {
            var data = body.data || {};
            headers = data.headers || [];
            fillSelect($('dm-col-id'), headers, data.suggest && data.suggest.id_card);
            fillSelect($('dm-col-addr'), headers, data.suggest && data.suggest.address);
            renderPreview(headers, data.rows || []);
            $('dm-preview-hint').textContent = '共读取 ' + (data.rows || []).length + ' 行预览';
        }).catch(function (err) {
            showToast(err.message, 'danger');
        }).finally(function () {
            setBusy(btn, false);
        });
    });

    function renderPreview(cols, rows) {
        var table = $('dm-preview-table');
        var thead = table.querySelector('thead');
        var tbody = table.querySelector('tbody');
        thead.innerHTML = '';
        tbody.innerHTML = '';
        var tr = document.createElement('tr');
        cols.forEach(function (name) { tr.appendChild(cell(name)); });
        thead.appendChild(tr);
        rows.forEach(function (row) {
            var line = document.createElement('tr');
            cols.forEach(function (name) { line.appendChild(cell(row[name])); });
            tbody.appendChild(line);
        });
        $('dm-preview-wrap').classList.remove('d-none');
    }

    $('dm-batch-btn').addEventListener('click', function () {
        var isUpload = $('dm-source-upload').checked;
        var btn = $('dm-batch-btn');
        var options;
        if (isUpload) {
            var file = $('dm-file').files[0];
            if (!file) {
                showToast('请先选择文件', 'warning');
                return;
            }
            var idCol = $('dm-col-id').value;
            var addrCol = $('dm-col-addr').value;
            if (!idCol && !addrCol) {
                showToast('请选择身份证列或地址列', 'warning');
                return;
            }
            var form = new FormData();
            form.append('file', file);
            form.append('id_card_col', idCol);
            form.append('address_col', addrCol);
            options = { method: 'POST', body: form };
        } else {
            options = {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source: 'students' })
            };
        }
        setBusy(btn, true, '启动中…');
        requestJSON(URLS.batch, options).then(function (body) {
            pollProgress(body.data.progress_id, function (item) {
                $('dm-progress-wrap').classList.remove('d-none');
                updateProgress(item);
            }, function (item) {
                $('dm-progress-wrap').classList.remove('d-none');
                updateProgress(item);
                if (item.status === 'error') {
                    showToast(item.message || '批量匹配失败', 'danger');
                    return;
                }
                showToast('批量匹配完成', 'success');
                if (item.result && item.result.batch_id) {
                    loadResults(item.result.batch_id);
                }
                loadBatches();
            });
        }).catch(function (err) {
            showToast(err.message, 'danger');
        }).finally(function () {
            setBusy(btn, false);
        });
    });

    function updateProgress(item) {
        var percent = item.total ? Math.round(item.done * 100 / item.total) : 0;
        var bar = $('dm-progress-bar');
        bar.style.width = percent + '%';
        bar.textContent = percent + '%';
        $('dm-progress-text').textContent =
            (item.phase ? '[' + item.phase + '] ' : '') +
            (item.message || '') + '（' + item.done + '/' + item.total + '）';
    }

    function pollProgress(progressId, onTick, onDone) {
        var url = URLS.progress.replace('__PID__', progressId);
        var timer = setInterval(function () {
            requestJSON(url, { method: 'GET' }).then(function (body) {
                var item = body.data || {};
                onTick(item);
                if (item.status !== 'running') {
                    clearInterval(timer);
                    onDone(item);
                }
            }).catch(function (err) {
                clearInterval(timer);
                showToast(err.message, 'danger');
            });
        }, 1000);
    }

    function loadResults(batchId) {
        requestJSON(withId(URLS.results, batchId) + '?size=100', { method: 'GET' })
            .then(function (body) {
                var data = body.data || {};
                var tbody = $('dm-result-table').querySelector('tbody');
                tbody.innerHTML = '';
                (data.items || []).forEach(function (item) {
                    var tr = document.createElement('tr');
                    tr.appendChild(cell(item.student_name));
                    tr.appendChild(cell(item.id_card));
                    tr.appendChild(cell(item.address));
                    tr.appendChild(cell(item.province_name));
                    tr.appendChild(cell(item.city_name));
                    tr.appendChild(cell(item.district_name));
                    var td = document.createElement('td');
                    td.appendChild(badge(item.match_status));
                    tr.appendChild(td);
                    tr.appendChild(cell(item.decision_path));
                    tbody.appendChild(tr);
                });
                var batch = data.batch || {};
                $('dm-result-title').textContent =
                    '批次 ' + (batch.batch_no || batchId) + '，共 ' + (data.total || 0) + ' 条，当前显示前 100 条';
                var link = $('dm-export-link');
                link.href = withId(URLS.exportUrl, batchId) + '?format=xlsx';
                link.classList.remove('d-none');
                $('dm-result-wrap').classList.remove('d-none');
            }).catch(function (err) {
                showToast(err.message, 'danger');
            });
    }

    /* ---------------------------------------------------------- 3. 批次记录 */
    function loadBatches() {
        requestJSON(URLS.batches + '?size=20', { method: 'GET' }).then(function (body) {
            var data = body.data || {};
            var tbody = $('dm-batch-table').querySelector('tbody');
            tbody.innerHTML = '';
            (data.items || []).forEach(function (item) {
                var tr = document.createElement('tr');
                tr.appendChild(cell(item.batch_no));
                tr.appendChild(cell(item.source));
                tr.appendChild(cell(item.total));
                var tdStatus = document.createElement('td');
                tdStatus.textContent = item.status || '';
                tr.appendChild(tdStatus);
                tr.appendChild(cell(item.created_by));
                tr.appendChild(cell(item.created_at));

                var td = document.createElement('td');
                td.className = 'text-end';
                var view = document.createElement('button');
                view.type = 'button';
                view.className = 'btn btn-outline-primary btn-sm me-1';
                view.textContent = '查看';
                view.addEventListener('click', function () { loadResults(item.id); });
                td.appendChild(view);

                var exportBtn = document.createElement('a');
                exportBtn.className = 'btn btn-outline-success btn-sm me-1';
                exportBtn.textContent = '导出';
                exportBtn.href = withId(URLS.exportUrl, item.id) + '?format=xlsx';
                td.appendChild(exportBtn);

                var del = document.createElement('button');
                del.type = 'button';
                del.className = 'btn btn-outline-danger btn-sm';
                del.textContent = '删除';
                del.addEventListener('click', function () {
                    showConfirm('确认删除批次 ' + item.batch_no + '？结果将软删除。', 'danger')
                        .then(function (ok) {
                            if (!ok) return;
                            requestJSON(withId(URLS.deleteUrl, item.id), { method: 'POST' })
                                .then(function () {
                                    showToast('已删除', 'success');
                                    loadBatches();
                                    $('dm-result-wrap').classList.add('d-none');
                                }).catch(function (err) { showToast(err.message, 'danger'); });
                        });
                });
                td.appendChild(del);
                tr.appendChild(td);
                tbody.appendChild(tr);
            });
        }).catch(function (err) {
            showToast(err.message, 'danger');
        });
    }

    $('dm-batches-refresh').addEventListener('click', loadBatches);

    /* ---------------------------------------------------------- 4. 字典状态 */
    function loadDictionary(verify) {
        var url = URLS.dictionary + (verify ? '?verify=1' : '');
        requestJSON(url, { method: 'GET' }).then(function (body) {
            var data = body.data || {};
            renderDictionary(data);
        }).catch(function (err) {
            showToast(err.message, 'danger');
        });
    }

    function line(label, value) {
        var row = document.createElement('div');
        row.className = 'd-flex justify-content-between border-bottom py-1';
        var left = document.createElement('span');
        left.className = 'text-muted';
        left.textContent = label;
        var right = document.createElement('span');
        right.textContent = value;
        row.appendChild(left);
        row.appendChild(right);
        return row;
    }

    function renderDictionary(data) {
        var box = $('dm-dict-body');
        box.innerHTML = '';
        var byLevel = data.by_level || {};
        var levelText = Object.keys(byLevel).sort().map(function (key) {
            return 'L' + key + ' ' + byLevel[key];
        }).join(' / ') || '-';
        box.appendChild(line('字典库', data.db_exists ? '已就绪' : '未导入'));
        box.appendChild(line('总行数', String(data.total || 0)));
        box.appendChild(line('各级行数', levelText));
        box.appendChild(line('库文件大小', ((data.db_size || 0) / 1024 / 1024).toFixed(1) + ' MB'));
        box.appendChild(line('更新时间', data.updated_at || '-'));
        var cache = data.cache || {};
        box.appendChild(line('内存缓存', cache.ready ? (cache.rows + ' 行（L1–L4）') : '未加载'));
        var pack = data.pack || {};
        box.appendChild(line('数据包', pack.exists ? ((pack.size / 1024 / 1024).toFixed(2) + ' MB') : '缺失'));
        if (pack.sha256) {
            box.appendChild(line('sha256', pack.sha256.slice(0, 16) + '…'));
        }
        if (pack.verify_ok !== undefined) {
            box.appendChild(line('数据包校验', pack.verify_ok ? '通过（行数一致）' : '不一致'));
        }
    }

    $('dm-refresh-dict').addEventListener('click', function () { loadDictionary(true); });

    $('dm-dict-import-btn').addEventListener('click', function () {
        showConfirm('导入会清空并重灌字典表（运行期只读库），确认继续？', 'warn')
            .then(function (ok) {
                if (!ok) return;
                var btn = $('dm-dict-import-btn');
                setBusy(btn, true, '启动中…');
                requestJSON(URLS.dictImport, { method: 'POST' })
                    .then(function (body) {
                        $('dm-progress-wrap').classList.remove('d-none');
                        pollProgress(body.data.progress_id, updateProgress, function (item) {
                            updateProgress(item);
                            setBusy(btn, false);
                            if (item.status === 'error') {
                                showToast(item.message || '导入失败', 'danger');
                            } else {
                                showToast('字典导入完成', 'success');
                                loadDictionary(true);
                            }
                        });
                    }).catch(function (err) {
                        setBusy(btn, false);
                        showToast(err.message, 'danger');
                    });
            });
    });

    function loadHistory() {
        requestJSON(URLS.history, { method: 'GET' }).then(function (body) {
            var tbody = $('dm-history-table').querySelector('tbody');
            tbody.innerHTML = '';
            ((body.data || {}).items || []).forEach(function (item) {
                var tr = document.createElement('tr');
                tr.appendChild(cell(item.old_code));
                tr.appendChild(cell(item.old_name));
                tr.appendChild(cell(item.new_code));
                tr.appendChild(cell(item.new_name));
                tbody.appendChild(tr);
            });
        }).catch(function (err) {
            showToast(err.message, 'danger');
        });
    }

    /* ---------------------------------------------------------- 批量补全 */
    $('dm-backfill-btn').addEventListener('click', function () {
        showConfirm('将按地址/身份证为“区县代码为空”的学生补全 7 列（最多 5000 条，不改更新时间）。确认继续？', 'warn')
            .then(function (ok) {
                if (!ok) return;
                var btn = $('dm-backfill-btn');
                setBusy(btn, true, '启动中…');
                requestJSON(URLS.backfill, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ only_empty: true, limit: 5000 })
                }).then(function (body) {
                    $('dm-progress-wrap').classList.remove('d-none');
                    pollProgress(body.data.progress_id, updateProgress, function (item) {
                        updateProgress(item);
                        setBusy(btn, false);
                        if (item.status === 'error') {
                            showToast(item.message || '回填失败', 'danger');
                        } else {
                            var res = item.result || {};
                            showToast('回填完成：更新 ' + (res.updated || 0) + ' 条', 'success');
                        }
                    });
                }).catch(function (err) {
                    setBusy(btn, false);
                    showToast(err.message, 'danger');
                });
            });
    });

    loadBatches();
    loadDictionary(false);
    loadHistory();
})();
