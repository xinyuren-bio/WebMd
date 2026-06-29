(function () {
  "use strict";

  var stage = null;
  var comp = null;
  var ffData = null;
  var highlightRepr = null;

  var section = document.getElementById("section-ligand-ff");
  var viewport = document.getElementById("ligand-ff-viewport");
  var hint = document.getElementById("ligand-ff-hint");
  var summaryEl = document.getElementById("ligand-ff-summary");
  var colorMode = document.getElementById("ligand-ff-color");
  var tabBar = document.getElementById("ligand-ff-tabs");
  var tableWrap = document.getElementById("ligand-ff-table-wrap");
  var btnReset = document.getElementById("ligand-ff-reset");
  var ffError = document.getElementById("ligand-ff-error");

  var TYPE_COLORS = [
    0xff6b6b, 0x4ecdc4, 0x45b7d1, 0xf9ca24, 0x6c5ce7,
    0xa29bfe, 0xfd79a8, 0x00b894, 0xe17055, 0x0984e3,
  ];

  // CPK 元素颜色（mol2 中 NGL 默认 element 着色常无法识别 O/N）
  var ELEMENT_COLORS = {
    H: 0xeeeeee,
    C: 0x909090,
    N: 0x3050f8,
    O: 0xff2010,
    S: 0xffff30,
    P: 0xff8000,
    F: 0x90e050,
    CL: 0x1ff01f,
    BR: 0xa62929,
    I: 0x940094,
  };

  function guessElement(n) {
    var s = (n || "").trim();
    if (!s) return "C";
    s = s.replace(/[0-9]+$/, "");
    if (s.length >= 2 && s.charAt(1) === s.charAt(1).toLowerCase()) {
      return (s.charAt(0).toUpperCase() + s.charAt(1).toLowerCase());
    }
    return s.charAt(0).toUpperCase();
  }

  function elementColor(el) {
    var k = (el || "C").toUpperCase();
    return ELEMENT_COLORS[k] !== undefined ? ELEMENT_COLORS[k] : 0xff00ff;
  }

  function buildIndexMaps(c, atoms) {
    var tmap = typeColorMap(atoms);
    var byId = {};
    atoms.forEach(function (a) { byId[a.id] = a; });

    var idx2charge = [];
    var idx2typeIdx = [];
    var idx2element = [];

    c.structure.eachAtom(function (ap) {
      var serial = ap.serial;
      if (!serial || serial < 1) serial = ap.index + 1;
      var fa = byId[serial] || atoms[ap.index];
      idx2charge[ap.index] = fa ? fa.charge : 0;
      idx2typeIdx[ap.index] = fa ? (tmap[fa.atom_type] || 0) : 0;
      idx2element[ap.index] = fa ? guessElement(fa.name) : guessElement(ap.atomname);
    });

    return { idx2charge: idx2charge, idx2typeIdx: idx2typeIdx, idx2element: idx2element };
  }

  // NGL 不支持 color: function()，须用 ColormakerRegistry.addScheme
  function registerColorScheme(tag, atomColorFn) {
    if (!window.NGL || !NGL.ColormakerRegistry) return "element";
    return NGL.ColormakerRegistry.addScheme(function () {
      this.atomColor = atomColorFn;
    }, tag);
  }

  // 电荷 → 颜色（红负蓝正，与界面说明一致）
  function lerpChannel(a, b, t) {
    return Math.round(a + (b - a) * t);
  }

  function chargeToColor(charge, minC, maxC) {
    var absMax = Math.max(Math.abs(minC), Math.abs(maxC), 1e-6);
    var t = Math.min(1, Math.abs(charge) / absMax);
    if (charge < 0) {
      return (
        (lerpChannel(255, 255, t) << 16) |
        (lerpChannel(255, 32, t) << 8) |
        lerpChannel(255, 32, t)
      );
    }
    if (charge > 0) {
      return (
        (lerpChannel(255, 48, t) << 16) |
        (lerpChannel(255, 80, t) << 8) |
        lerpChannel(255, 248, t)
      );
    }
    return 0xffffff;
  }

  function setHint(t) {
    if (hint) hint.textContent = t;
  }

  function clearError() {
    if (!ffError) return;
    ffError.textContent = "";
    ffError.classList.add("hidden");
  }

  function showError(msg) {
    if (!ffError) return;
    ffError.textContent = msg;
    ffError.classList.remove("hidden");
  }

  function formatErr(err, step) {
    var msg = err && err.message ? err.message : String(err);
    return (step ? "[" + step + "] " : "") + msg;
  }

  function safeAddRep(type, params, label, repErrors) {
    try {
      comp.addRepresentation(type, params);
    } catch (e) {
      var line = "添加 " + type + " 失败 (" + label + "): " + (e.message || e);
      repErrors.push(line);
    }
  }

  function typeColorMap(atoms) {
    var types = [];
    var map = {};
    atoms.forEach(function (a) {
      if (map[a.atom_type] === undefined) {
        map[a.atom_type] = types.length;
        types.push(a.atom_type);
      }
    });
    return map;
  }

  function waitNgl(cb, n) {
    n = n || 0;
    if (window.NGL) return cb();
    if (n > 50) { setHint("NGL 未加载"); return; }
    setTimeout(function () { waitNgl(cb, n + 1); }, 100);
  }

  function afterLayout(cb) {
    if (!section) return;
    section.classList.remove("hidden");
    requestAnimationFrame(function () {
      requestAnimationFrame(cb);
    });
  }

  function ensureStage() {
    if (stage) {
      stage.handleResize();
      return stage;
    }
    stage = new NGL.Stage("ligand-ff-viewport", { backgroundColor: "white" });
    window.addEventListener("resize", function () {
      if (stage) stage.handleResize();
    });
    stage.handleResize();
    return stage;
  }

  function clearHighlight() {
    if (highlightRepr && comp) {
      comp.removeRepresentation(highlightRepr);
      highlightRepr = null;
    }
  }

  function seleFromIds(ids) {
    return ids.map(function (id) { return "@" + id; }).join(" or ");
  }

  function highlightAtoms(ids, color) {
    if (!comp) return;
    clearHighlight();
    highlightRepr = comp.addRepresentation("ball+stick", {
      sele: seleFromIds(ids),
      colorScheme: "uniform",
      colorValue: color || 0xff3300,
      scale: 1.6,
      aspectRatio: 1.4,
    });
    comp.autoView(seleFromIds(ids), 600);
    if (stage && stage.viewer) stage.viewer.requestRender();
  }

  function applyColorMode(mode) {
    if (!comp || !ffData) return;
    clearError();
    comp.removeAllRepresentations();
    clearHighlight();

    var atoms = ffData.molecule.atoms;
    var maps = buildIndexMaps(comp, atoms);
    var repErrors = [];

    if (mode === "charge") {
      var minC = ffData.summary.charge_min;
      var maxC = ffData.summary.charge_max;
      var absMax = Math.max(Math.abs(minC), Math.abs(maxC), 1e-6);
      var atomData = new Float32Array(comp.structure.atomCount);
      maps.idx2charge.forEach(function (q, idx) {
        if (q !== undefined) atomData[idx] = q;
      });
      safeAddRep("ball+stick", {
        sele: "all",
        colorScheme: "value",
        colorScale: "rwb",
        colorDomain: [-absMax, absMax],
        colorData: { atomData: atomData },
      }, "部分电荷", repErrors);
    } else if (mode === "atomtype") {
      var t2 = maps.idx2typeIdx;
      var schemeId = registerColorScheme("ligand-ff-atomtype", function (atom) {
        var idx = t2[atom.index] || 0;
        return TYPE_COLORS[idx % TYPE_COLORS.length];
      });
      safeAddRep("ball+stick", {
        sele: "all",
        colorScheme: schemeId,
      }, "GAFF 类型", repErrors);
    } else {
      var e2 = maps.idx2element;
      var schemeIdEl = registerColorScheme("ligand-ff-element", function (atom) {
        var el = e2[atom.index] || guessElement(atom.atomname);
        return elementColor(el);
      });
      safeAddRep("ball+stick", {
        sele: "all",
        colorScheme: schemeIdEl,
      }, "元素颜色", repErrors);
    }

    safeAddRep("label", {
      sele: "all",
      labelType: "atomname",
      color: "black",
      radiusScale: 0.7,
      showBackground: false,
    }, "原子标签", repErrors);

    var reprCount = comp.reprList ? comp.reprList.length : (comp.representations || []).length;
    if (reprCount === 0) {
      showError("渲染失败：未成功创建任何表示方式。\n" + repErrors.join("\n"));
    } else if (repErrors.length > 0) {
      showError("部分表示方式失败：\n" + repErrors.join("\n"));
    }

    comp.autoView(800);
    if (stage) stage.handleResize();
    if (stage && stage.viewer) stage.viewer.requestRender();
  }

  function renderSummary() {
    if (!summaryEl || !ffData) return;
    var s = ffData.summary;
    summaryEl.innerHTML = [
      "<span>原子 <b>" + s.atom_count + "</b></span>",
      "<span>键 <b>" + s.bond_count + "</b></span>",
      "<span>电荷范围 <b>" + s.charge_min + " ~ " + s.charge_max + " e</b></span>",
      "<span>总电荷 <b>" + s.charge_sum + " e</b></span>",
      "<span>GAFF 类型 <b>" + s.atom_types.join(", ") + "</b></span>",
    ].join("");
  }

  function makeTable(headers, rows, onRowClick) {
    var html = "<table class='ff-table'><thead><tr>";
    headers.forEach(function (h) { html += "<th>" + h + "</th>"; });
    html += "</tr></thead><tbody>";
    rows.forEach(function (row, idx) {
      html += "<tr data-idx='" + idx + "'>";
      row.forEach(function (cell) { html += "<td>" + cell + "</td>"; });
      html += "</tr>";
    });
    html += "</tbody></table>";
    tableWrap.innerHTML = html;
    tableWrap.querySelectorAll("tbody tr").forEach(function (tr) {
      tr.addEventListener("click", function () {
        tableWrap.querySelectorAll("tbody tr").forEach(function (r) {
          r.classList.remove("active");
        });
        tr.classList.add("active");
        onRowClick(parseInt(tr.getAttribute("data-idx"), 10));
      });
    });
  }

  function showTab(name) {
    if (!ffData || !tableWrap) return;
    var mol = ffData.molecule;
    var frc = ffData.frcmod;

    if (name === "atoms") {
      makeTable(
        ["#", "名称", "GAFF 类型", "电荷 (e)", "坐标 (Å)"],
        mol.atoms.map(function (a) {
          return [
            a.id,
            a.name,
            a.atom_type,
            a.charge.toFixed(4),
            a.x.toFixed(2) + ", " + a.y.toFixed(2) + ", " + a.z.toFixed(2),
          ];
        }),
        function (idx) {
          highlightAtoms([mol.atoms[idx].id]);
        }
      );
    } else if (name === "bonds") {
      makeTable(
        ["#", "原子对", "键级"],
        mol.bonds.map(function (b) {
          return [b.id, b.label, b.order];
        }),
        function (idx) {
          var b = mol.bonds[idx];
          highlightAtoms([b.atom1, b.atom2]);
        }
      );
    } else if (name === "angles") {
      var rows = mol.angle_instances.length ? mol.angle_instances : frc.angle;
      makeTable(
        ["类型/原子", "k (kcal/mol/rad²)", "θ₀ (°)", "备注"],
        rows.map(function (r) {
          var label = r.label || r.type;
          return [label, r.k, r.theta0, (r.comment || "").slice(0, 40)];
        }),
        function (idx) {
          var r = rows[idx];
          if (r.atoms) highlightAtoms(r.atoms, 0x00aa88);
        }
      );
    } else if (name === "impropers") {
      var imps = mol.improper_instances.length ? mol.improper_instances : frc.improper;
      makeTable(
        ["类型/原子", "V/2", "n", "γ (°)", "备注"],
        imps.map(function (r) {
          var label = r.label || r.type;
          return [
            label,
            r.v1 !== undefined ? r.v1 : "-",
            r.n !== undefined ? r.n : "-",
            r.gamma !== undefined ? r.gamma : "-",
            (r.comment || "").slice(0, 30),
          ];
        }),
        function (idx) {
          var r = imps[idx];
          if (r.atoms) highlightAtoms(r.atoms, 0x8855ff);
        }
      );
    }
  }

  function bindTabs() {
    if (!tabBar) return;
    tabBar.querySelectorAll("button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        tabBar.querySelectorAll("button").forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        showTab(btn.getAttribute("data-tab"));
      });
    });
  }

  function load(taskId) {
    if (!section) return;
    clearError();

    waitNgl(function () {
      setHint("正在加载配体力场…");
      afterLayout(function () {
        try {
          ensureStage();
        } catch (err) {
          showError(formatErr(err, "初始化"));
          setHint("可视化初始化失败");
          return;
        }

        var ffUrl = "/api/tasks/" + taskId + "/ligand/forcefield";
        var molUrl = "/api/tasks/" + taskId + "/ligand/structure.mol2";

        Promise.all([
          fetch(ffUrl).then(function (r) {
            if (!r.ok) throw new Error("力场数据 HTTP " + r.status);
            return r.json();
          }),
          fetch(molUrl).then(function (r) {
            if (!r.ok) throw new Error("mol2 HTTP " + r.status);
            return r.text();
          }),
        ])
          .then(function (res) {
            ffData = res[0];
            if (comp) stage.removeComponent(comp);
            return stage.loadFile(new Blob([res[1]], { type: "text/plain" }), {
              ext: "mol2",
              defaultRepresentation: false,
            });
          })
          .then(function (c) {
            comp = c;
            renderSummary();
            bindTabs();
            applyColorMode(colorMode ? colorMode.value : "charge");
            showTab("atoms");
            setHint("点击表格行可在左侧 3D 视图中高亮对应原子/键角");
          })
          .catch(function (err) {
            var msg = formatErr(err, "加载");
            showError(msg);
            setHint("配体力场加载失败");
          });
      });
    });
  }

  function hide() {
    if (section) section.classList.add("hidden");
    if (comp && stage) {
      stage.removeComponent(comp);
      comp = null;
    }
    ffData = null;
    clearError();
    if (tableWrap) tableWrap.innerHTML = "";
  }

  if (colorMode) {
    colorMode.addEventListener("change", function () {
      try {
        applyColorMode(colorMode.value);
      } catch (e) {
        showError(formatErr(e, "切换着色"));
      }
    });
  }

  if (btnReset) {
    btnReset.addEventListener("click", function () {
      clearHighlight();
      if (comp) {
        comp.autoView(800);
        if (stage && stage.viewer) stage.viewer.requestRender();
      }
    });
  }

  window.LigandFF = { load: load, hide: hide };
})();
