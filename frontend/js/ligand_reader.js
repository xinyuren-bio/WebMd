/**
 * 配体阅读器：JSME 二维编辑（模仿 Molecule Search）。
 */
(function () {
  "use strict";

  var section = document.getElementById("section-ligand-reader");
  var btnPrepare = document.getElementById("btn-ligand-prepare");
  var btnAddH = document.getElementById("btn-ligand-add-h");
  var btnStripH = document.getElementById("btn-ligand-strip-h");
  var btnApply = document.getElementById("btn-jsme-apply");
  var btnConfirm = document.getElementById("btn-ligand-confirm");
  var btnSmilesLoad = document.getElementById("btn-smiles-load");
  var smilesInput = document.getElementById("ligand-smiles-input");
  var metaEl = document.getElementById("ligand-reader-meta");
  var tabsEl = document.getElementById("ligand-reader-tabs");
  var warnEl = document.getElementById("ligand-reader-warn");
  var statusEl = document.getElementById("ligand-reader-status");

  var ligands = [];
  var activeIndex = 0;
  var proteinPdbText = "";
  var confirmed = false;
  var jsmeApplet = null;
  var jsmeReady = false;
  var syncingEditor = false;

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

  function onJsmeChange() {
    if (!jsmeApplet || syncingEditor) return;
    try {
      var smi = jsmeApplet.smiles();
      if (smilesInput) smilesInput.value = smi || "";
    } catch (e) {
      /* ignore */
    }
  }

  /**
   * JSME 全局回调：模块加载完成后创建画板。
   */
  window.jsmeOnLoad = function () {
    try {
      jsmeApplet = new JSApplet.JSME("jsme-applet", "100%", "480px", {
        options: "newlook,hydrogens,reaction,query",
      });
      jsmeApplet.setCallBack("AfterStructureModified", onJsmeChange);
      jsmeReady = true;
      if (ligands[activeIndex] && ligands[activeIndex].mol2) {
        loadMol2IntoJsme(ligands[activeIndex].mol2);
      }
    } catch (e) {
      showWarn("JSME 初始化失败: " + (e.message || e));
    }
  };

  function loadMolIntoJsme(molText, smiles) {
    if (!jsmeApplet) return;
    syncingEditor = true;
    try {
      if (molText) {
        jsmeApplet.readMolFile(molText);
      } else if (smiles) {
        jsmeApplet.readGenericMolecularInput(smiles);
      }
      if (smilesInput && smiles) smilesInput.value = smiles;
    } catch (e) {
      showWarn("加载到画板失败: " + (e.message || e));
    } finally {
      setTimeout(function () {
        syncingEditor = false;
      }, 100);
    }
  }

  async function loadMol2IntoJsme(mol2Text) {
    if (!jsmeReady || !jsmeApplet) return;
    try {
      var resp = await apiFetch("/api/ligand/to-editor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mol2: mol2Text }),
      });
      if (!resp.ok) {
        var err = await resp.json().catch(function () {
          return {};
        });
        throw new Error(err.detail || "转编辑格式失败");
      }
      var data = await resp.json();
      loadMolIntoJsme(data.mol || "", data.smiles || "");
    } catch (e) {
      showWarn(e.message || String(e));
    }
  }

  function showActiveLigand() {
    var L = ligands[activeIndex];
    updateMeta();
    if (!L || !L.mol2) return;
    loadMol2IntoJsme(L.mol2);
    var warns = (L.warnings || []).join("；");
    showWarn(warns);
    if (statusEl && !confirmed) {
      statusEl.textContent = "可在 JSME 画板改氢/改键，然后点「应用编辑到 MOL2」";
    }
  }

  function setButtonsEnabled(on) {
    if (btnAddH) btnAddH.disabled = !on;
    if (btnStripH) btnStripH.disabled = !on;
    if (btnApply) btnApply.disabled = !on;
    if (btnConfirm) btnConfirm.disabled = !on;
    if (btnSmilesLoad) btnSmilesLoad.disabled = !on;
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
      if (statusEl) statusEl.textContent = "已生成结构，请在 JSME 中核对/编辑后应用并确认";
    } catch (e) {
      showWarn(e.message || String(e));
    } finally {
      if (btnPrepare) {
        btnPrepare.disabled = false;
        btnPrepare.textContent = "准备配体（拆分并补氢）";
      }
    }
  }

  async function runEdit(action) {
    var L = ligands[activeIndex];
    if (!L || !L.mol2) return;
    showWarn("");
    try {
      var resp = await apiFetch("/api/ligand/edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mol2: L.mol2, action: action }),
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
    }
  }

  async function applyJsmeToMol2() {
    if (!jsmeApplet) {
      showWarn("JSME 尚未就绪");
      return;
    }
    var L = ligands[activeIndex];
    if (!L) return;
    showWarn("");
    if (btnApply) {
      btnApply.disabled = true;
      btnApply.textContent = "应用中…";
    }
    try {
      var mol = "";
      var smi = "";
      try {
        mol = jsmeApplet.molFile() || "";
      } catch (e1) {
        mol = "";
      }
      try {
        smi = jsmeApplet.smiles() || "";
      } catch (e2) {
        smi = (smilesInput && smilesInput.value) || "";
      }
      if (!mol && !smi) throw new Error("画板为空，请先准备或绘制结构");

      var gen3dEl = document.getElementById("jsme-gen3d");
      var useGen3d = !!(gen3dEl && gen3dEl.checked);
      if (
        !useGen3d &&
        window.WebMD &&
        window.WebMD.isMol2ComplexMode &&
        window.WebMD.isMol2ComplexMode()
      ) {
        // 复合物默认不 gen3d：优先提示用户用补氢按钮保留对接坐标
        if (
          !window.confirm(
            "未勾选「重新生成 3D 坐标」。将尽量保留平面坐标写入 MOL2，可能不适合直接跑 MD。\n" +
              "若只需改氢，建议取消并用「全部补氢/去氢」。\n" +
              "若确认键连已改且可重新摆构，请勾选「重新生成 3D 坐标」后重试。\n\n仍要继续？"
          )
        ) {
          return;
        }
      }
      if (useGen3d) {
        if (
          !window.confirm(
            "重新生成三维坐标会丢失对接姿态。确认继续？"
          )
        ) {
          return;
        }
      }

      var resp = await apiFetch("/api/ligand/from-editor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mol: mol, smiles: smi, gen3d: useGen3d }),
      });
      if (!resp.ok) {
        var err = await resp.json().catch(function () {
          return {};
        });
        throw new Error(err.detail || "回写 MOL2 失败");
      }
      var data = await resp.json();
      var next = data.ligand || {};
      ligands[activeIndex] = Object.assign({}, L, next, {
        index: L.index,
        name: L.name || "ligand_edited.mol2",
        residue_key: L.residue_key,
      });
      setConfirmed(false);
      if (smilesInput && next.smiles) smilesInput.value = next.smiles;
      updateMeta();
      showWarn((next.warnings || []).join("；") || "已应用编辑到 MOL2");
      if (statusEl) statusEl.textContent = "编辑已应用到 MOL2，请核对画板后确认";
    } catch (e) {
      showWarn(e.message || String(e));
    } finally {
      if (btnApply) {
        btnApply.disabled = false;
        btnApply.textContent = "应用编辑到 MOL2";
      }
    }
  }

  function loadSmilesToJsme() {
    if (!jsmeApplet || !smilesInput) return;
    var smi = (smilesInput.value || "").trim();
    if (!smi) return;
    syncingEditor = true;
    try {
      jsmeApplet.readGenericMolecularInput(smi);
    } catch (e) {
      showWarn("SMILES 加载失败");
    } finally {
      setTimeout(function () {
        syncingEditor = false;
      }, 100);
    }
  }

  function confirmLigands() {
    if (!ligands.length) return;
    setConfirmed(true);
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
    var addH = document.getElementById("ligand-add-h");
    if (addH) addH.checked = false;
    if (statusEl) {
      statusEl.textContent = "已确认配体结构，可继续设置参数并提交任务";
      statusEl.classList.add("confirmed");
    }
  }

  function hideAndReset() {
    ligands = [];
    proteinPdbText = "";
    confirmed = false;
    setButtonsEnabled(false);
    if (window.WebMD) window.WebMD.confirmedLigandReader = null;
    if (statusEl) {
      statusEl.textContent = "尚未准备配体";
      statusEl.classList.remove("confirmed");
    }
    if (tabsEl) tabsEl.innerHTML = "";
    if (metaEl) metaEl.textContent = "";
    if (smilesInput) smilesInput.value = "";
    showWarn("");
  }

  if (btnPrepare) btnPrepare.addEventListener("click", runPrepare);
  if (btnAddH) btnAddH.addEventListener("click", function () { runEdit("add_h"); });
  if (btnStripH) btnStripH.addEventListener("click", function () { runEdit("strip_h"); });
  if (btnApply) btnApply.addEventListener("click", applyJsmeToMol2);
  if (btnConfirm) btnConfirm.addEventListener("click", confirmLigands);
  if (btnSmilesLoad) btnSmilesLoad.addEventListener("click", loadSmilesToJsme);

  window.LigandReader = {
    show: function () {
      if (section) section.classList.remove("hidden");
    },
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
