(function () {
  "use strict";

  var content = document.getElementById("jobs-content");

  function formatTime(ts) {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleString("zh-CN");
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function overallLabel(t) {
    if (t.status === "failed") return "前处理失败";
    if (t.status !== "completed") return t.status_label || "前处理中";
    if (t.payment_status === "paid") {
      return "已支付 · " + (t.md_status_label || t.md_status || "");
    }
    if (t.payment_status === "pending") return "付款待核实";
    return "前处理完成，待支付";
  }

  function renderList(items) {
    if (!content) return;
    if (!items.length) {
      content.innerHTML =
        '<p class="jobs-empty">暂无任务。请先在「体系准备」提交 Job。</p>'
        + '<p><a class="btn btn-primary" href="/#prepare">去提交任务</a></p>';
      return;
    }

    var html = '<ul class="jobs-list">';
    items.forEach(function (t) {
      var id = escapeHtml(t.task_id);
      var href = "/status.html?id=" + encodeURIComponent(t.task_id);
      var sim = t.simulation_time_ns != null ? t.simulation_time_ns + " ns" : "—";
      html +=
        '<li class="jobs-item">'
        + '<div class="jobs-item-main">'
        + '<a class="jobs-id" href="' + href + '">Job ID：<code>' + id + "</code></a>"
        + '<span class="jobs-status">' + escapeHtml(overallLabel(t)) + "</span>"
        + "</div>"
        + '<div class="jobs-item-meta">'
        + "<span>" + escapeHtml(t.ligand_label || "—") + "</span>"
        + "<span>" + escapeHtml(sim) + "</span>"
        + "<span>" + formatTime(t.created_at) + "</span>"
        + "</div>";
      if (t.status === "failed" && t.error_message) {
        html +=
          '<p class="jobs-error">' + escapeHtml(t.error_message) + "</p>";
      }
      html +=
        '<p class="jobs-item-actions">'
        + '<a href="' + href + '">查看进度</a>'
        + "</p>"
        + "</li>";
    });
    html += "</ul>";
    content.innerHTML = html;
  }

  function showNeedLogin() {
    if (!content) return;
    content.innerHTML =
      '<p class="jobs-empty">请先登录后查看您提交的 Job ID。</p>'
      + '<p><button type="button" class="btn btn-primary" id="jobs-btn-login">登录</button></p>';
    var btn = document.getElementById("jobs-btn-login");
    if (btn && window.WebMdAuth) {
      btn.addEventListener("click", function () {
        window.WebMdAuth.requireLogin();
      });
    }
  }

  function loadMyJobs() {
    if (!window.WebMdAuth || !window.WebMdAuth.getToken()) {
      showNeedLogin();
      return;
    }
    if (content) {
      content.innerHTML = '<p class="jobs-empty">加载中…</p>';
    }
    window.WebMdAuth.apiFetch("/api/tasks")
      .then(function (r) {
        if (r.status === 401) {
          showNeedLogin();
          return null;
        }
        if (!r.ok) {
          return r.json().then(function (e) {
            throw new Error(e.detail || "加载失败");
          });
        }
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        renderList(data.tasks || []);
      })
      .catch(function (e) {
        if (content) {
          content.innerHTML =
            '<p class="jobs-empty">' + escapeHtml(e.message || "加载失败") + "</p>";
        }
      });
  }

  window.addEventListener("webmd-auth-changed", loadMyJobs);
  loadMyJobs();
})();
