(function () {
  "use strict";

  // 样式预览：Tab 切换 + 分析模块勾选交互（无后端）
  var moduleCards = document.querySelectorAll(".interaction-module-card");
  moduleCards.forEach(function (card) {
    card.addEventListener("click", function (e) {
      if (e.target.tagName === "INPUT") return;
      var cb = card.querySelector('input[type="checkbox"]');
      if (cb) {
        cb.checked = !cb.checked;
        card.classList.toggle("selected", cb.checked);
      }
    });
    var cb = card.querySelector('input[type="checkbox"]');
    if (cb) {
      cb.addEventListener("change", function () {
        card.classList.toggle("selected", cb.checked);
      });
      card.classList.toggle("selected", cb.checked);
    }
  });

  var btnAll = document.getElementById("interaction-select-all");
  var btnCore = document.getElementById("interaction-select-core");
  var btnNone = document.getElementById("interaction-select-none");
  if (btnAll) {
    btnAll.addEventListener("click", function () {
      moduleCards.forEach(function (card) {
        var cb = card.querySelector('input[type="checkbox"]');
        if (cb) { cb.checked = true; card.classList.add("selected"); }
      });
    });
  }
  if (btnCore) {
    btnCore.addEventListener("click", function () {
      moduleCards.forEach(function (card) {
        var cb = card.querySelector('input[type="checkbox"]');
        var core = card.getAttribute("data-core") === "1";
        if (cb) {
          cb.checked = core;
          card.classList.toggle("selected", core);
        }
      });
    });
  }
  if (btnNone) {
    btnNone.addEventListener("click", function () {
      moduleCards.forEach(function (card) {
        var cb = card.querySelector('input[type="checkbox"]');
        if (cb) { cb.checked = false; card.classList.remove("selected"); }
      });
    });
  }

  var resultTabs = document.querySelectorAll(".interaction-result-tabs button");
  var resultPanels = document.querySelectorAll(".interaction-result-panel");
  resultTabs.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var tab = btn.getAttribute("data-tab");
      resultTabs.forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      resultPanels.forEach(function (p) {
        p.classList.toggle("active", p.getAttribute("data-panel") === tab);
      });
    });
  });
})();
