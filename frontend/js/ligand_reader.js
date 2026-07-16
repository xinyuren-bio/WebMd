/**
 * 配体阅读器：补氢预览、加/去 H、确认后供提交使用（类 CHARMM-GUI Ligand Reader）。
 */
(function () {
  "use strict";

  var section = document.getElementById("section-ligand-reader");
  var btnPrepare = document.getElementById("btn-ligand-prepare");
  var btnAddH = document.getElementById("btn-ligand-add-h");
  var btnStripH = document.getElementById("btn-ligand-strip-h");
  var btnConfirm = document.getElementById("btn-ligand-confirm");
  var metaEl = document.getElementById("ligand-reader-meta");
  var tabsEl = document.getElementById("ligand-reader-tabs");
  var warnEl = document.getElementById("ligand-reader-warn");
  var statusEl = document.getElementById("ligand-reader-status");
  var viewport = document.getElementById("ligand-reader-viewport");

  var stage = null;
  var ligComp = null;
  var ligands = [];
  var activeIndex = 0;
  var proteinPdbText = "";
  var confirmed = false;
  var pickBusy = false;

  function apiFetch(url, opts) {
    if (window.WebMdAuth && window.WebMdAuth.apiFetch) {
      return window.WebMdAuth.apiFetch(url, opts);
    }
    return fetch(url, opts);
  }

  function showWarn(msg) {
    if (!warnEl) return;
    if (!msg) {
      warnEl.textContent = "";
      warnEl.classList.add("hidden");
      return;
    }
    warnEl.textContent = msg;
    warnEl.classList.remove("hidden");
  }

  function setConfirmed(v) {
    confirmed = !!v;
    if (statusEl) {
      statusEl.classList.toggle("confirmed", confirmed);
      if (confirmed) {
        statusEl.textContent = "已确认配体结构，提交任务将使用此 MOL2（不再二次补氢）";
      }
    }
  }

  function updateMeta() {
    var L = ligands[activeIndex];
    if (!metaEl) return;
    if (!L) {
      metaEl.textContent = "";
      return;
    }
    var fc = L.formal_charge != null ? L.formal_charge : "—";
    metaEl.textContent =
      "原子 " +
      (L.n_atoms || 0) +
      " · 氢 " +
      (L.n_h || 0) +
      " · 重原子 " +
      (L.n_heavy || 0) +
      " · 形式电荷 " +
      fc;
  }

  function renderTabs() {
    if (!tabsEl) return;
    tabsEl.innerHTML = "";
    ligands.forEach(function (L, i) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "ligand-reader-tab" + (i === activeIndex ? " active" : "");
      b.textContent =
        "配体 " +
        (L.index || i + 1) +
        (L.residue_key ? " (" + L.residue_key + ")" : "");
      b.addEventListener("click", function () {
        activeIndex = i;
        renderTabs();
        showActiveLigand();
      });
      tabsEl.appendChild(b);
    });
  }

  function ensureStage() {
    if (!viewport || !window.NGL) return null;
    if (!stage) {
      stage = new NGL.Stage(viewport, { backgroundColor: "white" });
      window.addEventListener("resize", function () {
        if (stage) stage.handleResize();
      });
      stage.signals.clicked.add(onAtomClicked);
    }
    return stage;
  }

  function isHydrogenAtom(atom) {
    if (!atom) return false;
    var el = (atom.element || "").toUpperCase();
    if (el === "H" || el === "D") return true;
    var an = (atom.atomname || atom.name || "").toUpperCase();
    return an.charAt(0) === "H" && (an.length === 1 || /\d/.test(an.charAt(1)) || an.charAt(1) === "");
  }

  function onAtomClicked(pickingProxy) {
    if (!pickingProxy || !pickingProxy.atom || pickBusy) return;
    if (!ligands[activeIndex]) return;
    var atom = pickingProxy.atom;
    // NGL serial / index：优先使用 atom.serial（MOL2 常为 1-based）
    var atomId = atom.serial != null ? atom.serial : atom.index + 1;
    var isH = isHydrogenAtom(atom);
    var action = isH ? "remove_atom" : "add_h_on_atom";
    var tip = isH
      ? "删除氢原子 #" + atomId + "？"
      : "在原子 #" + atomId + " (" + (atom.atomname || atom.element) + ") 上补氢？";
    if (!window.confirm(tip)) return;
    runEdit(action, atomId);
  }

  function showActiveLigand() {
    var L = ligands[activeIndex];
    updateMeta();
    if (!L || !L.mol2) return;
    var st = ensureStage();
    if (!st) return;
    st.removeAllComponents();
    ligComp = null;
    var blob = new Blob([L.mol2], { type: "chemical/x-mol2" });
    st.loadFile(blob, { ext: "mol2", defaultRepresentation: false }).then(function (comp) {
      ligComp = comp;
      comp.addRepresentation("ball+stick", {
        sele: "not hydrogen",
        aspectRatio: 1.5,
        radiusScale: 0.8,
      });
      comp.addRepresentation("ball+stick", {
        sele: "hydrogen",
        color: "white",
        aspectRatio: 1.2,
        radiusScale: 0.55,
      });
      comp.addRepresentation("label", {
        sele: "not hydrogen",
        labelType: "atomname",
        color: "#334155",
        yOffset: 0.3,
        zOffset: 1.5,
        attachment: "middle-center",
        showBackground: true,
        backgroundColor: "white",
        backgroundOpacity: 0.55,
        scale: 0.9,
      });
      st.autoView(400);
    });
    var warns = (L.warnings || []).join("；");
    showWarn(warns);
    if (statusEl && !confirmed) {
      statusEl.textContent = "请核对结构后点击「确认配体结构」";
    }
  }

  function setButtonsEnabled(on) {
    if (btnAddH) btnAddH.disabled = !on;
    if (btnStripH) btnStripH.disabled = !on;
    if (btnConfirm) btnConfirm.disabled = !on;
  }

  function applyLigands(list, proteinText) {
    ligands = list || [];
    proteinPdbText = proteinText || "";
    activeIndex = 0;
    setConfirmed(false);
    setButtonsEnabled(ligands.length > 0);
    if (section) section.classList.remove("hidden");
    renderTabs();
    showActiveLigand();
  }

  async function runPrepare() {
    if (window.WebMdAuth && !window.WebMdAuth.requireLogin()) return;
    showWarn("");
    if (btnPrepare) {
      btnPrepare.disabled = true;
      btnPrepare.textContent = "准备中…";
    }
    try {
      var fd = new FormData();
      var isComplex =
        window.WebMD &&
        typeof window.WebMD.isMol2ComplexMode === "function" &&
        window.WebMD.isMol2ComplexMode();

      if (isComplex) {
        var pdb = document.getElementById("pdb-file");
        if (!pdb || !pdb.files[0]) throw new Error("请先上传复合物 PDB");
        var chains =
          window.WebMD.getSelectedProteinChains &&
          window.WebMD.getSelectedProteinChains();
        var ligs =
          window.WebMD.getSelectedLigandResidues &&
          window.WebMD.getSelectedLigandResidues();
        if (!chains || !chains.length) throw new Error("请选择蛋白链");
        if (!ligs || !ligs.length) throw new Error("请选择配体残基");
        fd.append("mode", "complex");
        fd.append("pdb_file", pdb.files[0]);
        fd.append("protein_chains", chains.join(","));
        fd.append("ligand_residues", ligs.join(","));
        fd.append("add_hydrogens", "1");
      } else {
        fd.append("mode", "mol2");
        fd.append("add_hydrogens", "1");
        if (window.WebMD && window.WebMD.appendMol2ToFormData) {
          window.WebMD.appendMol2ToFormData(fd);
        } else {
          var m1 = document.getElementById("mol2-file");
          if (!m1 || !m1.files[0]) throw new Error("请上传 MOL2");
          fd.append("mol2_file", m1.files[0]);
        }
      }

      var resp = await apiFetch("/api/ligand/prepare", { method: "POST", body: fd });
      if (!resp.ok) {
        var err = await resp.json().catch(function () {
          return {};
        });
        throw new Error(err.detail || "准备失败");
      }
      var data = await resp.json();
      applyLigands(data.ligands || [], data.protein_pdb || "");
      if (statusEl) statusEl.textContent = "已生成补氢结构，请核对后确认";
    } catch (e) {
      showWarn(e.message || String(e));
    } finally {
      if (btnPrepare) {
        btnPrepare.disabled = false;
        btnPrepare.textContent = "准备配体（拆分并补氢）";
      }
    }
  }

  async function runEdit(action, atomId) {
    var L = ligands[activeIndex];
    if (!L || !L.mol2) return;
    pickBusy = true;
    showWarn("");
    try {
      var body = { mol2: L.mol2, action: action };
      if (atomId != null) body.atom_id = atomId;
      var resp = await apiFetch("/api/ligand/edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        var err = await resp.json().catch(function () {
          return {};
        });
        throw new Error(err.detail || "编辑失败");
      }
      var data = await resp.json();
      var next = data.ligand || {};
      ligands[activeIndex] = Object.assign({}, L, next, {
        index: L.index,
        name: L.name,
        residue_key: L.residue_key,
        warnings: next.warnings || L.warnings || [],
      });
      setConfirmed(false);
      showActiveLigand();
    } catch (e) {
      showWarn(e.message || String(e));
    } finally {
      pickBusy = false;
    }
  }

  function confirmLigands() {
    if (!ligands.length) return;
    setConfirmed(true);
    // 供提交使用：蛋白 PDB（复合物模式）+ 确认后的 MOL2 文件
    var mol2Files = ligands.map(function (L, i) {
      return new File([L.mol2], L.name || "ligand_" + (i + 1) + ".mol2", {
        type: "chemical/x-mol2",
      });
    });
    var proteinFile = null;
    if (proteinPdbText) {
      proteinFile = new File([proteinPdbText], "protein_from_complex.pdb", {
        type: "chemical/x-pdb",
      });
    }
    window.WebMD = window.WebMD || {};
    window.WebMD.confirmedLigandReader = {
      confirmed: true,
      proteinFile: proteinFile,
      mol2Files: mol2Files,
      ligands: ligands,
    };
    // 关闭二次补氢，避免重复
    var addH = document.getElementById("ligand-add-h");
    if (addH) addH.checked = false;
    if (statusEl) {
      statusEl.textContent = "已确认配体结构，可继续设置参数并提交任务";
      statusEl.classList.add("confirmed");
    }
  }

  function showSectionForMol2() {
    if (!section) return;
    section.classList.remove("hidden");
  }

  function hideAndReset() {
    ligands = [];
    proteinPdbText = "";
    confirmed = false;
    setButtonsEnabled(false);
    if (window.WebMD) window.WebMD.confirmedLigandReader = null;
    if (stage) stage.removeAllComponents();
    if (statusEl) {
      statusEl.textContent = "尚未准备配体";
      statusEl.classList.remove("confirmed");
    }
    if (tabsEl) tabsEl.innerHTML = "";
    if (metaEl) metaEl.textContent = "";
    showWarn("");
  }

  if (btnPrepare) btnPrepare.addEventListener("click", runPrepare);
  if (btnAddH) btnAddH.addEventListener("click", function () { runEdit("add_h"); });
  if (btnStripH) btnStripH.addEventListener("click", function () { runEdit("strip_h"); });
  if (btnConfirm) btnConfirm.addEventListener("click", confirmLigands);

  window.LigandReader = {
    show: showSectionForMol2,
    hide: function () {
      if (section) section.classList.add("hidden");
      hideAndReset();
    },
    reset: hideAndReset,
    isConfirmed: function () {
      return !!(
        window.WebMD &&
        window.WebMD.confirmedLigandReader &&
        window.WebMD.confirmedLigandReader.confirmed
      );
    },
  };
})();
