(function () {
  "use strict";

  var stage = null;
  var currentComp = null;
  var debugLines = [];

  var sectionViewer = document.getElementById("section-viewer");
  var viewport = document.getElementById("ngl-viewport");
  var viewerHint = document.getElementById("viewer-hint");
  var viewerError = document.getElementById("viewer-error");
  var viewerDebugWrap = document.getElementById("viewer-debug-wrap");
  var viewerDebug = document.getElementById("viewer-debug");
  var styleSelect = document.getElementById("viewer-style");
  var btnResetView = document.getElementById("btn-reset-view");

  function setHint(t) {
    if (viewerHint) viewerHint.textContent = t;
  }

  function clearError() {
    if (!viewerError) return;
    viewerError.textContent = "";
    viewerError.classList.add("hidden");
  }

  function showError(msg) {
    if (!viewerError) return;
    viewerError.textContent = msg;
    viewerError.classList.remove("hidden");
  }

  function logDebug(key, val) {
    var line = key + ": " + val;
    debugLines.push(line);
    if (viewerDebug) viewerDebug.textContent = debugLines.join("\n");
    if (viewerDebugWrap) viewerDebugWrap.classList.remove("hidden");
  }

  function resetDebug() {
    debugLines = [];
    if (viewerDebug) viewerDebug.textContent = "";
    if (viewerDebugWrap) viewerDebugWrap.classList.add("hidden");
    clearError();
  }

  function formatErr(err, step) {
    var msg = err && err.message ? err.message : String(err);
    if (err && err.stack) {
      logDebug("错误堆栈", err.stack.split("\n").slice(0, 4).join(" | "));
    }
    return (step ? "[" + step + "] " : "") + msg;
  }

  function countAtoms(sele) {
    if (!currentComp || !currentComp.structure) return 0;
    var n = 0;
    try {
      var selection = new NGL.Selection(sele);
      currentComp.structure.eachAtom(function () { n += 1; }, selection);
    } catch (e) {
      logDebug("选择器计数失败 (" + sele + ")", e.message || e);
    }
    return n;
  }

  function checkWebGL() {
    try {
      var c = document.createElement("canvas");
      var gl = c.getContext("webgl") || c.getContext("experimental-webgl");
      if (!gl) return { ok: false, msg: "浏览器不支持 WebGL" };
      return { ok: true, msg: "WebGL 可用" };
    } catch (e) {
      return { ok: false, msg: "WebGL 检测异常: " + (e.message || e) };
    }
  }

  function getCanvasInfo() {
    if (!viewport) return "viewport 不存在";
    var canvas = viewport.querySelector("canvas");
    if (!canvas) return "canvas 未创建";
    var rect = viewport.getBoundingClientRect();
    return [
      "容器 " + Math.round(rect.width) + "×" + Math.round(rect.height) + " px",
      "canvas 属性 " + canvas.width + "×" + canvas.height,
      "canvas 显示 " + Math.round(canvas.clientWidth) + "×" + Math.round(canvas.clientHeight) + " px",
    ].join(" | ");
  }

  function waitForNgl(cb, n) {
    n = n || 0;
    if (window.NGL) {
      cb();
      return;
    }
    if (n > 50) {
      showError("NGL 库加载失败：/js/ngl.js 未成功加载，请强制刷新页面 (Cmd+Shift+R)");
      setHint("可视化不可用");
      return;
    }
    setTimeout(function () { waitForNgl(cb, n + 1); }, 100);
  }

  function afterLayout(cb) {
    if (!sectionViewer) return;
    sectionViewer.classList.remove("hidden");
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        cb();
      });
    });
  }

  function createStage() {
    if (!viewport) {
      throw new Error("找不到 #ngl-viewport 容器");
    }

    var webgl = checkWebGL();
    logDebug("WebGL", webgl.msg);
    if (!webgl.ok) {
      throw new Error(webgl.msg);
    }

    if (stage) {
      stage.handleResize();
      logDebug("Stage", "复用已有实例，已 handleResize");
      logDebug("Canvas", getCanvasInfo());
      return stage;
    }

    stage = new NGL.Stage("ngl-viewport", { backgroundColor: "white" });
    window.addEventListener("resize", function () {
      if (stage) stage.handleResize();
    });
    stage.handleResize();
    logDebug("Stage", "新建 NGL.Stage");
    logDebug("Canvas", getCanvasInfo());

    var rect = viewport.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) {
      throw new Error(
        "画布尺寸异常 (" + Math.round(rect.width) + "×" + Math.round(rect.height) +
        ")：容器可能在隐藏状态下初始化"
      );
    }

    return stage;
  }

  function addRep(type, params, label) {
    try {
      var repr = currentComp.addRepresentation(type, params);
      logDebug("表示方式", label + " → " + type + " | sele=" + (params.sele || "all"));
      return repr;
    } catch (e) {
      var errMsg = "添加 " + type + " 失败 (" + label + "): " + (e.message || e);
      logDebug("表示方式错误", errMsg);
      throw new Error(errMsg);
    }
  }

  function applyStyle(style) {
    if (!currentComp || !stage) {
      showError("无法应用样式：结构或 Stage 未就绪");
      return;
    }

    clearError();
    currentComp.removeAllRepresentations();

    var ligSele = "hetero and not (water or ion)";
    var protSele = "protein or (polymer and not hetero)";
    var nProt = countAtoms(protSele);
    var nLig = countAtoms(ligSele);
    var nAll = currentComp.structure.atomCount;

    logDebug("原子统计", "总计=" + nAll + " | 蛋白选择=" + nProt + " | 配体选择=" + nLig);

    var repErrors = [];

    function safeAdd(type, params, label) {
      try {
        addRep(type, params, label);
      } catch (e) {
        repErrors.push(e.message || String(e));
      }
    }

    if (style === "cartoon-ligand") {
      safeAdd("cartoon", { sele: protSele, color: "chainid" }, "蛋白");
      safeAdd("ball+stick", { sele: ligSele, color: "element" }, "配体");
    } else if (style === "surface-ligand") {
      safeAdd("surface", { sele: protSele, color: "chainid", opacity: 0.85 }, "蛋白表面");
      safeAdd("licorice", { sele: ligSele, color: "element" }, "配体");
    } else if (style === "licorice-all") {
      safeAdd("licorice", { sele: "all", color: "element" }, "全原子");
    } else {
      safeAdd("cartoon", { sele: protSele, color: "chainid" }, "蛋白");
      safeAdd("spacefill", { sele: ligSele, color: "element", scale: 0.35 }, "配体");
    }

    // 选择器未匹配时回退全结构
    if (nProt === 0 && nLig === 0) {
      logDebug("回退", "蛋白/配体选择器均为 0，改用 sele=all");
      safeAdd("cartoon", { sele: "all", color: "chainid" }, "全结构回退");
    } else if (nProt === 0) {
      logDebug("回退", "蛋白选择器为 0，对 all 使用 cartoon");
      safeAdd("cartoon", { sele: "all", color: "chainid" }, "蛋白回退");
    }

    var reprCount = currentComp.reprList ? currentComp.reprList.length : (currentComp.representations || []).length;
    logDebug("表示层数量", String(reprCount));
    logDebug("Canvas(渲染前)", getCanvasInfo());

    currentComp.autoView(800);
    stage.handleResize();
    if (stage.viewer) stage.viewer.requestRender();

    setTimeout(function () {
      logDebug("Canvas(渲染后)", getCanvasInfo());
      if (reprCount === 0) {
        showError("渲染失败：未成功创建任何表示方式。\n" + repErrors.join("\n"));
      } else if (repErrors.length > 0) {
        showError("部分表示方式失败：\n" + repErrors.join("\n"));
      } else {
        var canvas = viewport && viewport.querySelector("canvas");
        if (canvas && (canvas.width < 10 || canvas.height < 10)) {
          showError(
            "渲染异常：WebGL 画布尺寸为 " + canvas.width + "×" + canvas.height +
            "。\n请尝试放大浏览器窗口后点击「重置视角」，或刷新页面。"
          );
        }
      }
    }, 300);
  }

  function loadStructure(taskId) {
    if (!taskId || !sectionViewer) return;

    resetDebug();
    logDebug("任务 ID", taskId);

    waitForNgl(function () {
      setHint("正在加载复合物结构…");
      logDebug("NGL 版本", NGL.version || "未知");

      afterLayout(function () {
        try {
          var st = createStage();
          var url = "/api/tasks/" + taskId + "/structure/complex.pdb";
          logDebug("请求 URL", url);

          fetch(url)
            .then(function (resp) {
              logDebug("HTTP 状态", resp.status + " " + resp.statusText);
              if (!resp.ok) {
                throw new Error("请求失败 HTTP " + resp.status + " — " + url);
              }
              return resp.text();
            })
            .then(function (pdbText) {
              if (!pdbText || pdbText.length < 50) {
                throw new Error("PDB 内容为空或过短 (" + (pdbText ? pdbText.length : 0) + " 字符)");
              }
              logDebug("PDB 大小", pdbText.length + " 字符，前 40 字: " + pdbText.slice(0, 40).replace(/\n/g, " "));
              if (currentComp) st.removeComponent(currentComp);
              return st.loadFile(new Blob([pdbText], { type: "text/plain" }), {
                ext: "pdb",
                defaultRepresentation: false,
              });
            })
            .then(function (comp) {
              currentComp = comp;
              var n = comp.structure.atomCount;
              logDebug("解析结果", "atomCount=" + n + "，title=" + (comp.structure.title || "(无)"));
              if (!n || n <= 0) {
                throw new Error("PDB 解析后原子数为 0，请检查 complex.pdb 格式");
              }
              setHint("蛋白 + 配体（已去除水分子与离子）· 共 " + n + " 原子");
              applyStyle(styleSelect ? styleSelect.value : "cartoon-ligand");
            })
            .catch(function (err) {
              var msg = formatErr(err, "加载");
              showError(msg);
              setHint("可视化加载失败，请展开下方诊断信息");
              logDebug("最终错误", msg);
            });
        } catch (err) {
          var msg2 = formatErr(err, "初始化");
          showError(msg2);
          setHint("可视化初始化失败");
          logDebug("最终错误", msg2);
        }
      });
    });
  }

  function hideViewer() {
    if (sectionViewer) sectionViewer.classList.add("hidden");
    if (currentComp && stage) {
      stage.removeComponent(currentComp);
      currentComp = null;
    }
    resetDebug();
  }

  if (styleSelect) {
    styleSelect.addEventListener("change", function () {
      try {
        applyStyle(styleSelect.value);
      } catch (e) {
        showError(formatErr(e, "切换样式"));
      }
    });
  }

  if (btnResetView) {
    btnResetView.addEventListener("click", function () {
      try {
        if (currentComp) {
          currentComp.autoView(800);
          if (stage) {
            stage.handleResize();
            if (stage.viewer) stage.viewer.requestRender();
          }
          logDebug("操作", "已重置视角");
          logDebug("Canvas", getCanvasInfo());
        }
      } catch (e) {
        showError(formatErr(e, "重置视角"));
      }
    });
  }

  window.MdViewer = {
    load: loadStructure,
    hide: hideViewer,
  };
})();
