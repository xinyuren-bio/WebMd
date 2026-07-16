(function () {
  "use strict";

  var navLinks = document.querySelectorAll(".workspace-nav");
  var introView = document.getElementById("view-intro");
  var appRoot = document.getElementById("app");
  var panels = document.querySelectorAll(".workspace-panel");
  var brandHome = document.getElementById("site-brand-home");

  function resizeViewers() {
    setTimeout(function () {
      if (window.stage && window.stage.handleResize) window.stage.handleResize();
    }, 80);
  }

  function setView(name, pushHash) {
    var valid =
      name === "prepare" || name === "analysis" || name === "guide" ? name : "intro";

    navLinks.forEach(function (btn) {
      var on = btn.getAttribute("data-workspace") === valid;
      btn.classList.toggle("active", on);
    });

    if (introView) {
      introView.classList.toggle("hidden", valid !== "intro");
      introView.classList.toggle("active", valid === "intro");
    }

    if (appRoot) {
      appRoot.classList.toggle("hidden", valid === "intro");
    }

    panels.forEach(function (p) {
      var show = p.getAttribute("data-workspace") === valid;
      p.classList.toggle("active", show);
      p.hidden = !show;
    });

    document.body.classList.remove(
      "workspace-intro",
      "workspace-prepare",
      "workspace-analysis",
      "workspace-guide"
    );
    document.body.classList.add("workspace-" + valid);

    if (pushHash !== false) {
      var h = valid === "intro" ? "" : "#" + valid;
      var url = location.pathname + location.search + h;
      if (location.href.split("#")[0] + h !== location.href && (h || location.hash)) {
        history.replaceState(null, "", url);
      } else if (!h && location.hash) {
        history.replaceState(null, "", location.pathname + location.search);
      }
    }

    if (valid !== "intro") resizeViewers();
    window.scrollTo(0, 0);
  }

  navLinks.forEach(function (btn) {
    btn.addEventListener("click", function () {
      setView(btn.getAttribute("data-workspace"), true);
    });
  });

  document.querySelectorAll("[data-go]").forEach(function (el) {
    el.addEventListener("click", function () {
      setView(el.getAttribute("data-go"), true);
    });
  });

  if (brandHome) {
    brandHome.addEventListener("click", function (e) {
      e.preventDefault();
      setView("intro", true);
    });
  }

  window.WebMD = window.WebMD || {};
  window.WebMD.switchView = function (name) { setView(name, true); };
  window.WebMD.goHome = function () { setView("intro", true); };
  window.WebMD.switchTab = window.WebMD.switchView;

  function hashToView() {
    var h = (location.hash || "").replace("#", "");
    if (h === "prepare" || h === "analysis" || h === "guide") {
      setView(h, false);
      return;
    }
    // 教程页内锚点（#guide-step-0 等）须保持 guide 视图，不能跳回首页
    if (h && h.indexOf("guide-") === 0) {
      setView("guide", false);
      requestAnimationFrame(function () {
        var el = document.getElementById(h);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      return;
    }
    setView("intro", false);
  }

  hashToView();
  window.addEventListener("hashchange", hashToView);
})();
