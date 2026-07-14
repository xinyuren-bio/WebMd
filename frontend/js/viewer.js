(function () {
  "use strict";

  var stage = null;
  var currentComp = null;
  var ligandComp = null;
  var ligandComps = [];
  var LIGAND_COLORS = ["#e11d48", "#2563eb", "#ca8a04"];
  var isLocalPreview = false;
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

  // 对齐 pymol_viz/docking_viz.py 的 Baker lab 马卡龙配色
  var BAKER_MACARON = [
    "#b0a3d1", "#99c7e0", "#a6d1b5", "#dedb8c",
    "#fadf8c", "#f7b07d", "#f5a37d",
  ];
  var BAKER_LIGAND_C = "#ffff00";   // PyMOL yellow
  var BAKER_POCKET_C = "#0089a8";   // PyMOL tv_blue
  var BAKER_SURFACE = "#cccccc";    // PyMOL gray80
  var BAKER_BG_GREY = "#bfbfbf";    // style_pocket 背景灰卡通

  // Baker 配体 stick 元素着色（对齐 PyMOL _color_by_element，用 color 回调避免 Colormaker ES6 问题）
  function guessElementFromName(n) {
    var s = (n || "").trim();
    if (!s) return "C";
    s = s.replace(/[0-9]+$/, "");
    if (s.length >= 2 && s.charAt(1) === s.charAt(1).toLowerCase()) {
      return s.charAt(0).toUpperCase() + s.charAt(1).toLowerCase();
    }
    return s.charAt(0).toUpperCase();
  }

  function bakerLigandColor(p, carbonHex) {
    var e = (p.element || guessElementFromName(p.atomname) || "C").toUpperCase();
    if (e === "C") return hexToInt(carbonHex);
    if (e === "O") return 0xff0000;
    if (e === "N") return 0x0000ff;
    if (e === "S") return 0xffff00;
    if (e === "P") return 0xff8800;
    if (e === "F" || e === "CL" || e === "BR") return 0x808080;
    return 0xcccccc;
  }

  function hexToInt(hex) {
    return parseInt(String(hex).replace("#", ""), 16);
  }

  // 配体 stick：单层 licorice；NGL 须用 addScheme，不能 color: function()
  function addLigandSticks(ligSele, safeAdd, label, opts) {
    opts = opts || {};
    if (!ligSele || countAtoms(ligSele) === 0) return;

    var heavySele = ligSele + " and not hydrogen";
    if (countAtoms(heavySele) === 0) heavySele = ligSele;
    var carbonHex = opts.carbonColor || BAKER_LIGAND_C;

    var schemeId = "element";
    if (window.NGL && NGL.ColormakerRegistry) {
      schemeId = NGL.ColormakerRegistry.addScheme(function () {
        this.atomColor = function (atom) {
          return bakerLigandColor(atom, carbonHex);
        };
      }, "baker-ligand-stick");
    }

    safeAdd("licorice", {
      sele: heavySele,
      colorScheme: schemeId,
      multipleBond: true,
      radius: opts.radius || 0.2,
    }, label);
  }

  function buildDistancePocketSele(comp, ligSele, proteinSele, radius) {
    if (!comp || !comp.structure || countAtoms(ligSele) === 0) return "";
    try {
      var ligSelection = new NGL.Selection(ligSele);
      var nearSet = comp.structure.getAtomSetWithinSelection(ligSelection, radius);
      var proteinSet = comp.structure.getAtomSet(new NGL.Selection(proteinSele));
      var pocketAtoms = nearSet.clone();
      pocketAtoms.intersection(proteinSet);
      var pocketRes = comp.structure.getAtomSetWithinGroup(pocketAtoms);
      var seleStr = pocketRes.toSeleString();
      return seleStr && seleStr !== "NONE" ? seleStr : "";
    } catch (e) {
      logDebug("距离口袋", "构建失败: " + (e.message || e));
      return "";
    }
  }

  // 对齐 detect_interacting_residues：氢键 + 盐桥残基
  function detectInteractingPocketSele(comp, ligSele, proteinSele) {
    if (!comp || !comp.structure || countAtoms(ligSele) === 0) return "";
    try {
      var structure = comp.structure;
      var ligSelection = new NGL.Selection(ligSele);

      var nearH = structure.getAtomSetWithinSelection(ligSelection, 3.5);
      var polarProtein = structure.getAtomSet(new NGL.Selection(
        proteinSele + " and (_O or _N or _S)"
      ));
      var polarNear = nearH.clone();
      polarNear.intersection(polarProtein);
      var polarRes = structure.getAtomSetWithinGroup(polarNear);

      var nearSalt = structure.getAtomSetWithinSelection(ligSelection, 4.0);
      var charged = structure.getAtomSet(new NGL.Selection(
        proteinSele + " and (ASP or GLU or LYS or ARG or HIS)"
      ));
      var saltNear = nearSalt.clone();
      saltNear.intersection(charged);
      var saltRes = structure.getAtomSetWithinGroup(saltNear);

      var combined = polarRes.clone();
      combined.union(saltRes);
      if (combined.getSize() === 0) return "";

      var seleStr = combined.toSeleString();
      return seleStr && seleStr !== "NONE" ? seleStr : "";
    } catch (e) {
      logDebug("相互作用口袋", "检测失败: " + (e.message || e));
      return "";
    }
  }

  function collectProteinChains(comp, protSele) {
    var chains = [];
    var seen = {};
    try {
      comp.structure.eachPolymer(function (polymer) {
        var chainId = polymer.chainProxy.chainname || "";
        if (seen[chainId]) return;
        var chainSele = chainId.trim()
          ? "(" + protSele + ") and :" + chainId.trim()
          : protSele;
        if (countAtoms(chainSele) > 0) {
          seen[chainId] = true;
          chains.push(chainId);
        }
      });
    } catch (e) {
      logDebug("链检测", e.message || e);
    }
    return chains;
  }

  function chainSelection(protSele, chainId) {
    if (chainId && chainId.trim()) {
      return "(" + protSele + ") and :" + chainId.trim();
    }
    return protSele;
  }

  // 口袋侧链：ball+stick 保持键完整
  function addConnectedBallStick(sele, safeAdd, label, opts) {
    opts = opts || {};
    if (!sele || countAtoms(sele) === 0) return;

    var heavySele = sele + " and not hydrogen";
    if (countAtoms(heavySele) === 0) heavySele = sele;

    var params = {
      sele: heavySele,
      color: opts.color || "element",
      aspectRatio: opts.aspectRatio || 1.2,
      multipleBond: true,
      opacity: 1,
    };
    if (opts.colorValue) params.colorValue = opts.colorValue;
    if (opts.radius) params.radius = opts.radius;

    safeAdd("ball+stick", params, label);
  }

  // style_overall：马卡龙卡通 + 半透明表面 + 配体 stick
  function applyBakerOverall(ligSele, protSele, safeAdd) {
    var chains = collectProteinChains(currentComp, protSele);
    if (chains.length === 0) chains = [""];

    chains.forEach(function (chainId, idx) {
      var sele = chainSelection(protSele, chainId);
      var color = BAKER_MACARON[idx % BAKER_MACARON.length];
      var chainLabel = chainId.trim() || "all";

      safeAdd("cartoon", {
        sele: sele,
        color: "uniform",
        colorValue: color,
        aspectRatio: 3,
      }, "卡通 " + chainLabel);

      safeAdd("surface", {
        sele: sele,
        color: "uniform",
        colorValue: color,
        opacity: 0.6,
        surfaceType: "ms",
        side: "front",
      }, "表面 " + chainLabel);
    });

    addLigandSticks(ligSele, safeAdd, "配体", { radius: 0.2 });
  }

  // style_pocket：灰背景卡通 + 口袋表面/侧链 + 黄色配体 + 极性接触 + 标签
  function applyBakerPocket(ligSele, protSele, safeAdd) {
    var pocketSele = detectInteractingPocketSele(currentComp, ligSele, protSele);
    var pocketMode = "interaction";

    if (!pocketSele || countAtoms(pocketSele) === 0) {
      pocketSele = buildDistancePocketSele(currentComp, ligSele, protSele, 5.0);
      pocketMode = "distance";
    }

    var nPocket = pocketSele ? countAtoms(pocketSele) : 0;
    var nLigHeavy = countAtoms(ligSele + " and not hydrogen");
    logDebug("Baker 口袋", pocketMode + " 模式，口袋原子=" + nPocket + "，配体重原子=" + nLigHeavy);

    safeAdd("cartoon", {
      sele: protSele,
      color: "uniform",
      colorValue: BAKER_BG_GREY,
      opacity: 0.45,
      side: "front",
    }, "背景蛋白卡通");

    if (pocketSele && nPocket > 0) {
      safeAdd("surface", {
        sele: pocketSele,
        color: "uniform",
        colorValue: BAKER_SURFACE,
        opacity: 0.35,
        surfaceType: "ms",
        side: "front",
      }, "口袋表面");

      var pocketSide = "(" + pocketSele + ") and sidechain";
      addConnectedBallStick(pocketSide, safeAdd, "口袋侧链", {
        aspectRatio: 1.2,
      });

      safeAdd("contact", {
        sele: ligSele + " or " + pocketSele,
        contactType: "polar",
        maxDistance: 3.5,
        linewidth: 2.5,
        opacity: 0.9,
      }, "极性接触");

      safeAdd("label", {
        sele: pocketSele + " and .CA",
        labelType: "residue",
        color: "black",
        fontSize: 14,
      }, "残基标签");
    }

    // 配体最后渲染，避免被半透明蛋白/表面遮挡
    addLigandSticks(ligSele, safeAdd, "配体", { radius: 0.22 });

    if (nLigHeavy > 0) {
      return pocketSele && nPocket > 0
        ? "(" + ligSele + ") or (" + pocketSele + ")"
        : ligSele;
    }
    return pocketSele || ligSele;
  }

  function clearComponents() {
    if (stage) {
      if (currentComp) {
        stage.removeComponent(currentComp);
        currentComp = null;
      }
      ligandComps.forEach(function (c) {
        stage.removeComponent(c);
      });
      ligandComps = [];
      ligandComp = null;
    }
  }

  function normalizeMol2Files(mol2File) {
    if (!mol2File) return [];
    if (Array.isArray(mol2File)) {
      return mol2File.filter(Boolean).slice(0, 3);
    }
    return [mol2File];
  }

  function applyDualStyle(style) {
    if (!currentComp || !ligandComps.length || !stage) return;

    clearError();
    currentComp.removeAllRepresentations();
    ligandComps.forEach(function (lc) {
      lc.removeAllRepresentations();
    });

    var protSele = "protein or (polymer and not hetero)";
    var repErrors = [];

    function addProt(type, params, label) {
      try {
        currentComp.addRepresentation(type, Object.assign({ sele: protSele }, params));
      } catch (e) {
        repErrors.push((label || type) + ": " + (e.message || e));
      }
    }

    // 配体按元素着色；多配体时仅碳原子用不同底色区分（O/N/S 等仍走元素色）
    function ligColorScheme(carbonHex) {
      if (window.NGL && NGL.ColormakerRegistry) {
        return NGL.ColormakerRegistry.addScheme(function () {
          this.atomColor = function (atom) {
            return bakerLigandColor(atom, carbonHex);
          };
        }, "dual-ligand-element");
      }
      return "element";
    }

    function addLig(comp, color, label) {
      try {
        var schemeId = ligColorScheme(color);
        var base = { sele: "all", colorScheme: schemeId };
        if (style === "baker-overall" || style === "baker-pocket") {
          comp.addRepresentation("ball+stick", Object.assign({}, base, { radius: 0.22 }));
        } else if (style === "surface-ligand" || style === "licorice-all") {
          comp.addRepresentation("licorice", base);
        } else if (style === "cartoon-spacefill") {
          comp.addRepresentation("spacefill", Object.assign({}, base, { scale: 0.35 }));
        } else {
          comp.addRepresentation("ball+stick", base);
        }
      } catch (e) {
        repErrors.push((label || "配体") + ": " + (e.message || e));
      }
    }

    if (style === "baker-overall") {
      addProt("cartoon", { color: "chainid" }, "蛋白");
      addProt("surface", { color: "chainid", opacity: 0.35 }, "表面");
    } else if (style === "baker-pocket") {
      addProt("cartoon", { color: "chainid", opacity: 0.5 }, "蛋白");
      addProt("licorice", { sele: "protein", color: "element", radius: 0.12 }, "口袋");
    } else if (style === "surface-ligand") {
      addProt("surface", { color: "chainid", opacity: 0.85 }, "蛋白表面");
    } else if (style === "licorice-all") {
      addProt("licorice", { color: "element" }, "蛋白");
    } else if (style === "cartoon-spacefill") {
      addProt("cartoon", { color: "chainid" }, "蛋白");
    } else {
      addProt("cartoon", { color: "chainid" }, "蛋白");
    }

    ligandComps.forEach(function (lc, idx) {
      addLig(lc, LIGAND_COLORS[idx % LIGAND_COLORS.length], "配体" + (idx + 1));
    });

    stage.autoView(800);
    stage.handleResize();
    if (stage.viewer) stage.viewer.requestRender();

    if (repErrors.length) {
      showError("部分表示方式失败：\n" + repErrors.join("\n"));
    }
  }

  function applyStyle(style) {
    if (isLocalPreview && currentComp && ligandComps.length) {
      applyDualStyle(style);
      return;
    }

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

    var focusSele = null;

    if (style === "baker-overall") {
      applyBakerOverall(ligSele, protSele, safeAdd);
    } else if (style === "baker-pocket") {
      focusSele = applyBakerPocket(ligSele, protSele, safeAdd);
    } else if (style === "cartoon-ligand") {
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

    if (focusSele && countAtoms(focusSele) > 0) {
      currentComp.autoView(focusSele, 800);
    } else if (style === "baker-pocket" && nLig > 0) {
      currentComp.autoView(ligSele, 800);
    } else {
      currentComp.autoView(800);
    }
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

    isLocalPreview = false;
    resetDebug();
    logDebug("任务 ID", taskId);

    waitForNgl(function () {
      setHint("正在加载处理后复合物…");
      logDebug("NGL 版本", NGL.version || "未知");

      afterLayout(function () {
        try {
          var st = createStage();
          clearComponents();
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
              setHint("处理后复合物 · 共 " + n + " 原子");
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

  function loadFromFiles(pdbFile, mol2File) {
    var mol2Files = normalizeMol2Files(mol2File);
    if (!pdbFile || !mol2Files.length || !sectionViewer) return;

    isLocalPreview = true;
    resetDebug();
    logDebug("预览模式", "本地上传 PDB + " + mol2Files.length + " 个 MOL2");

    waitForNgl(function () {
      setHint("正在加载上传结构…");

      afterLayout(function () {
        try {
          var st = createStage();
          clearComponents();

          var promises = [
            st.loadFile(pdbFile, { ext: "pdb", defaultRepresentation: false }),
          ];
          mol2Files.forEach(function (f) {
            promises.push(st.loadFile(f, { ext: "mol2", defaultRepresentation: false }));
          });

          Promise.all(promises)
            .then(function (comps) {
              currentComp = comps[0];
              ligandComps = comps.slice(1);
              ligandComp = ligandComps[0] || null;
              var nProt = currentComp.structure.atomCount;
              var nLigTotal = ligandComps.reduce(function (s, c) {
                return s + c.structure.atomCount;
              }, 0);
              logDebug(
                "预览解析",
                "蛋白原子=" + nProt + " | 配体数=" + ligandComps.length + " | 配体原子合计=" + nLigTotal
              );
              if (!nProt) {
                throw new Error("PDB 解析后蛋白原子数为 0");
              }
              if (!ligandComps.length) {
                throw new Error("MOL2 解析失败");
              }
              sectionViewer.classList.remove("hidden");
              setHint(
                "预览 · 蛋白 + " +
                  ligandComps.length +
                  " 个配体（按元素着色；多配体碳色 " +
                  LIGAND_COLORS.slice(0, ligandComps.length).join(" / ") +
                  "）"
              );
              applyDualStyle(styleSelect ? styleSelect.value : "cartoon-ligand");
            })
            .catch(function (err) {
              var msg = formatErr(err, "本地上传预览");
              showError(msg);
              setHint("预览加载失败");
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
    clearComponents();
    isLocalPreview = false;
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
        if (stage) {
          if (isLocalPreview && ligandComps.length) {
            stage.autoView(800);
          } else if (currentComp) {
            currentComp.autoView(800);
          }
          stage.handleResize();
          if (stage.viewer) stage.viewer.requestRender();
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
    loadFromFiles: loadFromFiles,
    hide: hideViewer,
    hasLocalPreview: function () {
      return isLocalPreview && currentComp && ligandComps.length > 0;
    },
  };
})();
