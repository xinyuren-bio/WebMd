(function () {
  "use strict";

  var keyInput = document.getElementById("admin-key");
  var btnLoad = document.getElementById("btn-load");
  var content = document.getElementById("admin-content");
  var userStatsEl = document.getElementById("user-stats");
  var msgEl = document.getElementById("admin-msg-top") || document.getElementById("admin-msg");

  var STORAGE_KEY = "webmd_admin_key";
  var MARKET_URL = "https://www.autodl.com/market/list";

  function showMsg(text, ok) {
    if (!msgEl) return;
    msgEl.textContent = text;
    msgEl.classList.remove("hidden", "ok");
    if (ok) msgEl.classList.add("ok");
  }

  function formatTime(ts) {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleString("zh-CN");
  }

  function getKey() {
    return keyInput ? keyInput.value.trim() : "";
  }

  function loadPending() {
    var k = getKey();
    if (!k) {
      showMsg("请先输入管理员密钥");
      return;
    }
    try { localStorage.setItem(STORAGE_KEY, k); } catch (e) {}

    Promise.all([
      fetch("/api/admin/payments/pending?admin_key=" + encodeURIComponent(k)).then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) { throw new Error(e.detail || "加载失败"); });
        }
        return r.json();
      }),
      fetch("/api/admin/users/stats?admin_key=" + encodeURIComponent(k)).then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) { throw new Error(e.detail || "用户统计加载失败"); });
        }
        return r.json();
      }),
      fetch("/api/admin/md/queue?admin_key=" + encodeURIComponent(k)).then(function (r) {
        if (!r.ok) return { items: [] };
        return r.json();
      }),
    ])
      .then(function (results) {
        var payData = results[0];
        var userData = results[1];
        var mdData = results[2];
        if (mdData.market_url) MARKET_URL = mdData.market_url;
        renderUserStats(userData);
        renderList(payData.items || [], k);
        renderMdQueue(mdData.items || [], k);
        showMsg("已刷新：注册用户 " + (userData.total || 0) + " 人，待核实 " + (payData.items || []).length + " 条", true);
      })
      .catch(function (err) {
        if (content) content.innerHTML = '<p class="admin-empty">' + (err.message || "加载失败") + "</p>";
        if (userStatsEl) userStatsEl.innerHTML = "";
        showMsg(err.message || "加载失败");
      });
  }

  function renderUserStats(data) {
    if (!userStatsEl) return;
    var total = data.total != null ? data.total : 0;
    var recent = data.recent || [];
    var html = '<div class="stat-cards">'
      + '<div class="stat-card"><div class="label">累计注册用户</div><div class="value">' + total + "</div></div>"
      + "</div>";

    html += '<h2 class="admin-section-title">最近注册用户</h2>';
    if (!recent.length) {
      html += '<p class="admin-empty">暂无注册用户</p>';
    } else {
      html += '<table class="admin-table"><thead><tr>'
        + "<th>邮箱</th><th>用户 ID</th><th>完成模拟数</th><th>注册时间</th>"
        + "</tr></thead><tbody>";
      recent.forEach(function (u) {
        html += "<tr><td>" + (u.email || "—") + "</td><td><code>"
          + (u.user_id || "—") + "</code></td><td>"
          + (u.md_completed != null ? u.md_completed : 0)
          + "</td><td>" + formatTime(u.created_at) + "</td></tr>";
      });
      html += "</tbody></table>";
    }
    var byNs = data.md_by_ns || {};
    if (data.md_completed_total != null || byNs["10"] != null) {
      html += '<h2 class="admin-section-title">模拟完成分档</h2>';
      html += '<div class="stat-cards">'
        + '<div class="stat-card"><div class="label">MD 完成总数</div><div class="value">'
        + (data.md_completed_total || 0) + "</div></div>"
        + '<div class="stat-card"><div class="label">10 ns</div><div class="value">'
        + (byNs["10"] || 0) + "</div></div>"
        + '<div class="stat-card"><div class="label">100 ns</div><div class="value">'
        + (byNs["100"] || 0) + "</div></div>"
        + '<div class="stat-card"><div class="label">200 ns</div><div class="value">'
        + (byNs["200"] || 0) + "</div></div>"
        + "</div>";
    }
    userStatsEl.innerHTML = html;
  }

  function apiDetail(e) {
    // 解析 FastAPI 错误详情（字符串或校验错误数组）
    if (!e) return "";
    if (typeof e.detail === "string") return e.detail;
    if (Array.isArray(e.detail) && e.detail[0] && e.detail[0].msg) {
      return e.detail.map(function (x) { return x.msg; }).join("; ");
    }
    return e.detail ? String(e.detail) : "";
  }

  function reject(taskId, k) {
    // 与「核实通过」一致先用 confirm；避免仅用 prompt 时点取消被误认为按钮无响应
    if (!confirm("确认驳回任务 " + taskId + " 的支付申请？\n用户可重新发起支付。")) {
      return;
    }
    var reason = "未收到对应金额转账";
    try {
      var typed = window.prompt("驳回原因（可改，直接确定即可）：", reason);
      // 点取消仍按默认原因驳回，避免「点了不通过却什么都没发生」
      if (typed !== null && String(typed).trim()) {
        reason = String(typed).trim();
      }
    } catch (e) {
      // 个别环境禁用 prompt，忽略并使用默认原因
    }

    fetch(
      "/api/admin/payments/" +
        encodeURIComponent(taskId) +
        "/reject?admin_key=" +
        encodeURIComponent(k) +
        "&reason=" +
        encodeURIComponent(reason),
      { method: "POST" }
    )
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) {
            throw new Error(apiDetail(e) || "驳回失败");
          });
        }
        return r.json();
      })
      .then(function () {
        showMsg("任务 " + taskId + " 已驳回，用户可重新支付", true);
        if (msgEl && msgEl.scrollIntoView) {
          msgEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
        loadPending();
      })
      .catch(function (err) {
        showMsg(err.message || "驳回失败");
        if (msgEl && msgEl.scrollIntoView) {
          msgEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      });
  }

  function saveAutodlSsh(taskId, k) {
    var row = document.querySelector('.md-row[data-id="' + taskId + '"]');
    if (!row) return;
    var sshEl = row.querySelector(".ssh-cmd");
    var pwdEl = row.querySelector(".ssh-pwd");
    var sidEl = row.querySelector(".ssh-server-id");
    var sshCmd = sshEl ? sshEl.value.trim() : "";
    var pwd = pwdEl ? pwdEl.value : "";
    var serverId = sidEl ? sidEl.value.trim() : "";
    if (!serverId || !sshCmd || !pwd) {
      showMsg("请填写实例 ID、SSH 命令和密码");
      return;
    }

    fetch("/api/admin/tasks/" + taskId + "/autodl?admin_key=" + encodeURIComponent(k), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ssh_command: sshCmd, password: pwd, server_id: serverId }),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) { throw new Error(e.detail || "保存失败"); });
        }
        return r.json();
      })
      .then(function () {
        showMsg("任务 " + taskId + " SSH 已保存，正在自动提交 MD…", true);
        if (pwdEl) pwdEl.value = "";
        loadPending();
      })
      .catch(function (err) {
        showMsg(err.message || "保存失败");
      });
  }

  function dispatchMd(taskId, k) {
    fetch("/api/admin/md/" + taskId + "/dispatch?admin_key=" + encodeURIComponent(k), {
      method: "POST",
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) { throw new Error(e.detail || "提交失败"); });
        }
        return r.json();
      })
      .then(function () {
        showMsg("任务 " + taskId + " 已触发 AutoDL 提交", true);
        loadPending();
      })
      .catch(function (err) {
        showMsg(err.message || "提交失败");
      });
  }

  function renderMdQueue(items, k) {
    var el = document.getElementById("md-queue-content");
    if (!el) return;
    if (!items.length) {
      el.innerHTML = '<p class="admin-empty">暂无排队或运行中的 MD 任务</p>';
      return;
    }

    var html = '<p class="hint" style="margin-bottom:12px;">收到邮件后请前往 <a href="' + MARKET_URL + '" target="_blank" rel="noopener">AutoDL 算力市场</a> 开机，再为对应任务粘贴 SSH 与密码。</p>';

    items.forEach(function (item) {
      var sshVal = item.ssh_command || "";
      var sidVal = item.server_id || "";
      var hostInfo = item.ssh_configured
        ? (item.ssh_host + ":" + item.ssh_port + (item.server_id ? " · 实例 " + item.server_id : ""))
        : "未配置";
      html += '<div class="md-row admin-md-card" data-id="' + item.task_id + '">'
        + '<p><strong>任务</strong> <code>' + item.task_id + "</code> · "
        + (item.user_email || "—") + " · "
        + (item.simulation_time_ns != null ? item.simulation_time_ns + " ns" : "—")
        + " · <strong>" + (item.atom_count || "—") + " 原子</strong></p>"
        + "<p>MD 状态：" + (item.md_status_label || item.md_status)
        + " · SSH：" + hostInfo + "</p>";

      if (item.error_message) {
        html += '<p class="admin-empty" style="padding:0;color:#c0392b;">' + item.error_message + "</p>";
      }

      if (item.md_status === "queued" || item.md_status === "failed") {
        html += '<div class="admin-ssh-row">'
          + '<input type="text" class="ssh-server-id payment-payer-note" placeholder="AutoDL 实例 ID" value="' + sidVal.replace(/"/g, "&quot;") + '">'
          + '<input type="text" class="ssh-cmd payment-payer-note" placeholder="ssh -p 50977 root@connect.westx.seetacloud.com" value="' + sshVal.replace(/"/g, "&quot;") + '">'
          + '<input type="password" class="ssh-pwd payment-payer-note" placeholder="SSH 密码" autocomplete="off">'
          + '<button type="button" class="btn btn-primary btn-save-ssh">保存并提交</button>';
        if (item.ssh_configured) {
          html += '<button type="button" class="btn btn-secondary btn-retry">重试提交</button>';
        }
        html += "</div>";
      }
      html += "</div>";
    });

    el.innerHTML = html;

    el.querySelectorAll(".btn-save-ssh").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var row = btn.closest(".md-row");
        if (row) saveAutodlSsh(row.getAttribute("data-id"), k);
      });
    });
    el.querySelectorAll(".btn-retry").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var row = btn.closest(".md-row");
        if (row) dispatchMd(row.getAttribute("data-id"), k);
      });
    });
  }

  function approve(taskId, k) {
    if (!confirm("确认已收到任务 " + taskId + " 的转账？")) return;

    fetch("/api/admin/payments/" + taskId + "/approve?admin_key=" + encodeURIComponent(k), {
      method: "POST",
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) { throw new Error(e.detail || "批准失败"); });
        }
        return r.json();
      })
      .then(function () {
        showMsg("任务 " + taskId + " 已批准，已发送 AutoDL 开机邮件", true);
        loadPending();
      })
      .catch(function (err) {
        showMsg(err.message || "批准失败");
      });
  }

  function renderList(items, k) {
    if (!content) return;
    if (!items.length) {
      content.innerHTML = '<p class="admin-empty">暂无待核实支付</p>';
      return;
    }

    var html = '<table class="admin-table"><thead><tr>'
      + "<th>任务 ID</th><th>用户</th><th>金额</th><th>时长(ns)</th><th>备注</th><th>提交时间</th><th>操作</th>"
      + "</tr></thead><tbody>";

    items.forEach(function (item) {
      html += "<tr>"
        + "<td><code>" + item.task_id + "</code></td>"
        + "<td>" + (item.user_email || item.user_id || "—") + "</td>"
        + "<td>¥" + Number(item.payment_amount).toFixed(2) + "</td>"
        + "<td>" + (item.simulation_time_ns != null ? item.simulation_time_ns : "—") + "</td>"
        + "<td>" + (item.payment_note || "—") + "</td>"
        + "<td>" + formatTime(item.payment_claimed_at) + "</td>"
        + '<td><button type="button" class="btn btn-success btn-approve" data-id="' + item.task_id + '">核实通过</button> '
        + '<button type="button" class="btn btn-danger btn-reject" data-id="' + item.task_id + '">核实不通过</button></td>'
        + "</tr>";
    });

    html += "</tbody></table>";
    content.innerHTML = html;

    content.querySelectorAll(".btn-approve").forEach(function (btn) {
      btn.addEventListener("click", function () {
        approve(btn.getAttribute("data-id"), k);
      });
    });
    content.querySelectorAll(".btn-reject").forEach(function (btn) {
      btn.addEventListener("click", function () {
        reject(btn.getAttribute("data-id"), k);
      });
    });
  }

  if (btnLoad) btnLoad.addEventListener("click", loadPending);

  try {
    var saved = localStorage.getItem(STORAGE_KEY);
    if (saved && keyInput) keyInput.value = saved;
  } catch (e) {}
})();
