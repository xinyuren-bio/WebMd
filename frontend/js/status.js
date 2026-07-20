(function () {
  "use strict";

  var params = new URLSearchParams(location.search);
  var taskId = params.get("id") || "";
  var content = document.getElementById("status-content");
  var taskIdEl = document.getElementById("status-task-id");
  var pollTimer = null;

  function formatTime(ts) {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleString("zh-CN");
  }

  function statusText(d) {
    if (d.status === "failed") return "前处理失败";
    if (d.status !== "completed") return d.status_label || "前处理中";
    if (d.payment_status === "paid") return "已支付 · " + (d.md_status_label || "");
    if (d.payment_status === "pending") return "付款待核实";
    return "前处理完成，待支付";
  }

  function render(d) {
    if (!content) return;
    var html = "<div class=\"status-card\">"
      + "<p><strong>任务状态</strong>：" + statusText(d) + "</p>"
      + "<p><strong>前处理</strong>：" + (d.status_label || d.status) + "</p>";

    if (d.simulation_time_ns != null) {
      html += "<p><strong>模拟时长</strong>：" + d.simulation_time_ns + " ns</p>";
    }
    if (d.status !== "completed" && d.status !== "failed" && d.status !== "awaiting_peptide_sequence") {
      html += "<p class=\"status-hint\">前处理可能排队执行；完成后会通知到您的注册邮箱，无需一直停留在本页。</p>";
    }
    if (d.status === "awaiting_peptide_sequence") {
      html += "<p class=\"status-hint\">需要您在网站确认肽序列后才能继续，请登录首页或打开任务页完成操作。</p>";
    }
    if (d.status === "completed") {
      if (d.payment_status === "unpaid" && d.can_pay) {
        var payAmt = d.payment_amount != null ? Number(d.payment_amount).toFixed(0) : "150/240";
        html += "<p class=\"status-hint\">请登录网站完成 ¥" + payAmt + " 支付后，方可下载文件包并启动 MD 模拟。</p>";
        // 带 Job ID 深链，避免落到 /prepare 静态 404，并自动打开支付弹窗
        html += "<p><a class=\"btn btn-primary\" href=\"/?task="
          + encodeURIComponent(taskId)
          + "#prepare\">前往支付</a></p>";
      } else if (d.payment_status === "pending") {
        html += "<p class=\"status-hint\">已提交付款，等待管理员核实，核实后将自动开始模拟。</p>";
      } else if (d.can_download) {
        html += "<p class=\"status-hint\">支付已核实。请登录网站下载文件包。</p>";
        html += "<p><strong>MD</strong>：" + (d.md_status_label || d.md_status) + "</p>";
        if (d.atom_count) {
          html += "<p><strong>体系原子数</strong>：" + d.atom_count + "</p>";
        }
        if (d.analysis_summary) {
          html += "<pre class=\"status-analysis\">" + d.analysis_summary.replace(/</g, "&lt;") + "</pre>";
        }
      }
    }
    html += "<p class=\"status-meta\">提交时间：" + formatTime(d.created_at) + "</p>";
    html += "</div>";
    content.innerHTML = html;
  }

  function load() {
    if (!taskId) {
      if (content) content.innerHTML = "<p class=\"status-empty\">缺少任务 ID</p>";
      return;
    }
    if (taskIdEl) taskIdEl.textContent = taskId;

    fetch("/api/tasks/" + taskId + "/public")
      .then(function (r) {
        if (!r.ok) throw new Error("任务不存在");
        return r.json();
      })
      .then(function (d) {
        render(d);
        if (d.status !== "completed" && d.status !== "failed") {
          if (!pollTimer) pollTimer = setInterval(load, 3000);
        } else if (d.payment_status !== "paid" || d.md_status === "running" || d.md_status === "queued") {
          if (!pollTimer) pollTimer = setInterval(load, 5000);
        } else if (d.md_status !== "completed" && d.md_status !== "failed") {
          if (!pollTimer) pollTimer = setInterval(load, 8000);
        } else {
          clearInterval(pollTimer);
        }
      })
      .catch(function (e) {
        if (content) content.innerHTML = "<p class=\"status-empty\">" + (e.message || "加载失败") + "</p>";
      });
  }

  load();
})();
