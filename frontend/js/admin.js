(function () {
  "use strict";

  var keyInput = document.getElementById("admin-key");
  var btnLoad = document.getElementById("btn-load");
  var content = document.getElementById("admin-content");
  var msgEl = document.getElementById("admin-msg");

  var STORAGE_KEY = "webmd_admin_key";

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

    fetch("/api/admin/payments/pending?admin_key=" + encodeURIComponent(k))
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) {
            throw new Error(e.detail || "加载失败");
          });
        }
        return r.json();
      })
      .then(function (data) {
        renderList(data.items || [], k);
        showMsg("已刷新，共 " + (data.items || []).length + " 条待核实", true);
      })
      .catch(function (err) {
        if (content) content.innerHTML = '<p class="admin-empty">' + (err.message || "加载失败") + "</p>";
        showMsg(err.message || "加载失败");
      });
  }

  function approve(taskId, k) {
    if (!confirm("确认已收到任务 " + taskId + " 的转账？")) return;

    fetch("/api/admin/payments/" + taskId + "/approve?admin_key=" + encodeURIComponent(k), {
      method: "POST",
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) {
            throw new Error(e.detail || "批准失败");
          });
        }
        return r.json();
      })
      .then(function () {
        showMsg("任务 " + taskId + " 已批准", true);
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
      + "<th>任务 ID</th><th>应付金额</th><th>用户备注</th><th>提交时间</th><th>操作</th>"
      + "</tr></thead><tbody>";

    items.forEach(function (item) {
      html += "<tr>"
        + "<td><code>" + item.task_id + "</code></td>"
        + "<td>¥" + Number(item.payment_amount).toFixed(2) + "</td>"
        + "<td>" + (item.payment_note || "—") + "</td>"
        + "<td>" + formatTime(item.payment_claimed_at) + "</td>"
        + '<td><button type="button" class="btn btn-success btn-approve" data-id="' + item.task_id + '">核实通过</button></td>'
        + "</tr>";
    });

    html += "</tbody></table>";
    content.innerHTML = html;

    content.querySelectorAll(".btn-approve").forEach(function (btn) {
      btn.addEventListener("click", function () {
        approve(btn.getAttribute("data-id"), k);
      });
    });
  }

  if (btnLoad) btnLoad.addEventListener("click", loadPending);

  try {
    var saved = localStorage.getItem(STORAGE_KEY);
    if (saved && keyInput) keyInput.value = saved;
  } catch (e) {}
})();
