(function () {
  "use strict";

  var keyInput = document.getElementById("admin-key");
  var btnLoad = document.getElementById("btn-load");
  var content = document.getElementById("stats-content");
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

  function loadStats() {
    var k = keyInput ? keyInput.value.trim() : "";
    if (!k) {
      showMsg("请先输入管理员密钥");
      return;
    }
    try { localStorage.setItem(STORAGE_KEY, k); } catch (e) {}

    fetch("/api/admin/analytics/stats?admin_key=" + encodeURIComponent(k))
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) {
            throw new Error(e.detail || "加载失败");
          });
        }
        return r.json();
      })
      .then(function (data) {
        return fetch("/api/admin/users/stats?admin_key=" + encodeURIComponent(k))
          .then(function (r2) {
            if (!r2.ok) return { total: 0, recent: [] };
            return r2.json();
          })
          .then(function (userData) {
            renderStats(data, userData);
            showMsg("数据已更新", true);
          });
      })
      .catch(function (err) {
        if (content) content.innerHTML = '<p class="admin-empty">' + (err.message || "加载失败") + "</p>";
        showMsg(err.message || "加载失败");
      });
  }

  function renderStats(d, userData) {
    if (!content) return;
    userData = userData || { total: 0, recent: [] };
    var byNs = d.md_by_ns || userData.md_by_ns || {};
    var mdTotal = d.md_completed_total != null
      ? d.md_completed_total
      : (userData.md_completed_total || 0);

    var html = '<div class="stat-cards">'
      + card("累计注册用户", userData.total)
      + card("总浏览量 PV", d.total_pv)
      + card("累计独立访客 UV", d.total_uv)
      + card("今日 PV", d.today_pv)
      + card("今日 UV", d.today_uv)
      + "</div>";

    html += '<h2 style="font-size:1rem;margin:0 0 8px;">模拟完成统计</h2>';
    html += '<div class="stat-cards">'
      + card("MD 完成总数", mdTotal)
      + card("10 ns 完成", byNs["10"] != null ? byNs["10"] : 0)
      + card("100 ns 完成", byNs["100"] != null ? byNs["100"] : 0)
      + card("200 ns 完成", byNs["200"] != null ? byNs["200"] : 0)
      + (byNs.other ? card("其他时长", byNs.other) : "")
      + "</div>";

    if (d.md_top_users && d.md_top_users.length) {
      html += '<h2 style="font-size:1rem;margin:0 0 8px;">用户模拟完成排行</h2>';
      html += "<table class=\"admin-table\"><thead><tr>"
        + "<th>邮箱</th><th>用户 ID</th><th>完成模拟数</th></tr></thead><tbody>";
      d.md_top_users.forEach(function (u) {
        html += "<tr><td>" + (u.email || "—") + "</td><td><code>"
          + (u.user_id || "—") + "</code></td><td>"
          + (u.md_completed != null ? u.md_completed : 0) + "</td></tr>";
      });
      html += "</tbody></table>";
    }

    html += "<h2 style=\"font-size:1rem;margin:0 0 8px;\">最近注册用户</h2>";
    if (!userData.recent || !userData.recent.length) {
      html += '<p class="admin-empty">暂无注册用户</p>';
    } else {
      html += "<table class=\"admin-table\"><thead><tr>"
        + "<th>邮箱</th><th>用户 ID</th><th>完成模拟数</th><th>注册时间</th>"
        + "</tr></thead><tbody>";
      userData.recent.forEach(function (u) {
        html += "<tr><td>" + (u.email || "—") + "</td><td><code>"
          + (u.user_id || "—") + "</code></td><td>"
          + (u.md_completed != null ? u.md_completed : 0)
          + "</td><td>" + formatTime(u.created_at) + "</td></tr>";
      });
      html += "</tbody></table>";
    }

    html += "<h2 style=\"font-size:1rem;margin:1.25rem 0 8px;\">近 7 日访问</h2>";
    html += "<table class=\"admin-table\"><thead><tr><th>日期</th><th>浏览量 PV</th><th>独立访客 UV</th></tr></thead><tbody>";
    (d.last7 || []).forEach(function (row) {
      html += "<tr><td>" + row.date + "</td><td>" + row.pv + "</td><td>" + row.uv + "</td></tr>";
    });
    html += "</tbody></table>";

    html += "<h2 style=\"font-size:1rem;margin:0 0 8px;\">页面排行</h2>";
    if (!d.top_pages || !d.top_pages.length) {
      html += '<p class="admin-empty">暂无页面数据</p>';
    } else {
      html += "<table class=\"admin-table\"><thead><tr><th>页面</th><th>浏览量</th></tr></thead><tbody>";
      d.top_pages.forEach(function (row) {
        html += "<tr><td><code>" + row.path + "</code></td><td>" + row.pv + "</td></tr>";
      });
      html += "</tbody></table>";
    }

    html += "<p class=\"hint\">首次访问：" + formatTime(d.first_visit_at)
      + " · 最近访问：" + formatTime(d.last_visit_at) + "</p>";

    content.innerHTML = html;
  }

  function card(label, value) {
    return '<div class="stat-card"><div class="label">' + label + '</div><div class="value">' + (value != null ? value : 0) + "</div></div>";
  }

  if (btnLoad) btnLoad.addEventListener("click", loadStats);

  try {
    var saved = localStorage.getItem(STORAGE_KEY);
    if (saved && keyInput) keyInput.value = saved;
  } catch (e) {}
})();
