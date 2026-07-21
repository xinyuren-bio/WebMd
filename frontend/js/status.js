(function () {
  "use strict";

  var params = new URLSearchParams(location.search);
  var taskId = params.get("id") || "";
  var content = document.getElementById("status-content");
  var taskIdEl = document.getElementById("status-task-id");
  var pollTimer = null;
  // 填写肽序列时避免轮询冲掉输入框
  var pepFormActive = false;
  var lastStatus = "";

  function formatTime(ts) {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleString("zh-CN");
  }

  function isLoggedIn() {
    return !!(window.WebMdAuth && window.WebMdAuth.getToken());
  }

  function statusText(d) {
    if (d.status === "failed") return "前处理失败";
    if (d.status !== "completed") return d.status_label || "前处理中";
    if (d.payment_status === "paid") return "已支付 · " + (d.md_status_label || "");
    if (d.payment_status === "pending") return "付款待核实";
    return "前处理完成，待支付";
  }

  function bindPeptideForm(d) {
    var input = document.getElementById("status-pep-input");
    var btn = document.getElementById("status-pep-submit");
    var btnLogin = document.getElementById("status-pep-login");
    var errEl = document.getElementById("status-pep-error");
    if (btnLogin) {
      btnLogin.addEventListener("click", function () {
        if (window.WebMdAuth) window.WebMdAuth.requireLogin();
      });
    }
    if (!btn || !input) return;
    btn.addEventListener("click", function () {
      submitPeptideSequence(input, btn, errEl);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        submitPeptideSequence(input, btn, errEl);
      }
    });
  }

  function submitPeptideSequence(input, btn, errEl) {
    if (!isLoggedIn()) {
      if (window.WebMdAuth) window.WebMdAuth.requireLogin();
      return;
    }
    var seq = (input.value || "").trim();
    if (errEl) errEl.textContent = "";
    if (!seq) {
      if (errEl) errEl.textContent = "请输入单字母肽序列";
      return;
    }
    btn.disabled = true;
    btn.textContent = "核实中…";
    window.WebMdAuth.apiFetch("/api/tasks/" + taskId + "/peptide-sequence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sequence: seq }),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, status: r.status, data: data };
        });
      })
      .then(function (res) {
        if (res.status === 401 || res.status === 403) {
          if (window.WebMdAuth) window.WebMdAuth.requireLogin();
          throw new Error(res.data.detail || "请使用提交该任务的账号登录");
        }
        if (!res.ok) {
          var detail = res.data.detail;
          if (Array.isArray(detail)) {
            detail = detail.map(function (x) { return x.msg || JSON.stringify(x); }).join("; ");
          }
          throw new Error(detail || "序列确认失败");
        }
        pepFormActive = false;
        if (errEl) errEl.textContent = "";
        if (content) {
          content.innerHTML =
            "<div class=\"status-card\"><p class=\"status-hint\">序列已提交，前处理继续中…</p></div>";
        }
        if (!pollTimer) pollTimer = setInterval(load, 3000);
        load();
      })
      .catch(function (e) {
        if (errEl) errEl.textContent = e.message || "确认失败";
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = "确认并继续";
      });
  }

  function render(d) {
    if (!content) return;
    // 用户正在填序列时，勿整页刷新冲掉输入
    if (pepFormActive && d.status === "awaiting_peptide_sequence" && lastStatus === d.status) {
      return;
    }
    lastStatus = d.status;

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
      pepFormActive = true;
      var hint =
        "检测到非标准肽 PDB（常见于对接导出）。请在下方输入单字母氨基酸序列；仅当组成与三维匹配全部通过后才会继续。";
      if (d.peptide_sequence_hint_n) {
        hint += " 结构中氮原子约 " + d.peptide_sequence_hint_n + " 个，可作长度参考。";
      }
      html += "<p class=\"status-hint\">" + hint + "</p>";
      html += "<div class=\"status-pep-box\">";
      html += "<label for=\"status-pep-input\">单字母序列（如 ACDEFG）</label>";
      html += "<input type=\"text\" id=\"status-pep-input\" maxlength=\"200\" autocomplete=\"off\" spellcheck=\"false\" placeholder=\"仅 A–Y 标准氨基酸\">";
      html += "<div class=\"status-pep-actions\">";
      if (!isLoggedIn()) {
        html += "<button type=\"button\" class=\"btn btn-secondary\" id=\"status-pep-login\">先登录</button>";
      }
      html += "<button type=\"button\" class=\"btn btn-primary\" id=\"status-pep-submit\">确认并继续</button>";
      html += "</div>";
      html += "<p class=\"status-pep-error\" id=\"status-pep-error\"></p>";
      html += "</div>";
    } else {
      pepFormActive = false;
    }
    if (d.status === "completed") {
      if (d.payment_status === "unpaid" && d.can_pay) {
        var payAmt = d.payment_amount != null ? Number(d.payment_amount).toFixed(2) : "147.70";
        if (Number(d.payment_amount) === 0 || (d.simulation_time_ns === 10 && d.can_pay)) {
          html += "<p class=\"status-hint\">10 ns 任务可使用免费额度解锁（每人 5 次）；请登录网站点击启动。</p>";
        } else {
          html += "<p class=\"status-hint\">请登录网站完成 ¥" + payAmt + " 支付后，方可下载文件包并启动 MD 模拟。</p>";
        }
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
    if (d.status === "awaiting_peptide_sequence") {
      bindPeptideForm(d);
    }
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
        if (d.status === "awaiting_peptide_sequence") {
          // 填序列阶段暂停轮询，避免冲掉输入；登录后刷新一次即可
          if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
          }
          return;
        }
        if (d.status !== "completed" && d.status !== "failed") {
          if (!pollTimer) pollTimer = setInterval(load, 3000);
        } else if (d.payment_status !== "paid" || d.md_status === "running" || d.md_status === "queued") {
          if (!pollTimer) pollTimer = setInterval(load, 5000);
        } else if (d.md_status !== "completed" && d.md_status !== "failed") {
          if (!pollTimer) pollTimer = setInterval(load, 8000);
        } else {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      })
      .catch(function (e) {
        if (content) content.innerHTML = "<p class=\"status-empty\">" + (e.message || "加载失败") + "</p>";
      });
  }

  window.addEventListener("webmd-auth-changed", function () {
    // 登录后重绘，显示「确认并继续」而无需跳转
    pepFormActive = false;
    load();
  });

  load();
})();
