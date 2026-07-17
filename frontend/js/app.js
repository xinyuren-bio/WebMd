(function () {
  "use strict";

  const pdbInput = document.getElementById("pdb-file");
  const mol2Input = document.getElementById("mol2-file");
  const pdbName = document.getElementById("pdb-name");
  const mol2Name = document.getElementById("mol2-name");
  const ligandTypeSel = document.getElementById("ligand-type");
  const ligandTypeHint = document.getElementById("ligand-type-hint");
  const peptideInput = document.getElementById("peptide-file");
  const peptideName = document.getElementById("peptide-file-name");
  const peptideLabel = document.getElementById("peptide-file-label");
  const pdbFileLabel = document.getElementById("pdb-file-label");
  const mol2Group = document.getElementById("ligand-mol2-group");
  const peptideGroup = document.getElementById("ligand-peptide-group");
  const mol2Actions = document.getElementById("ligand-mol2-actions");
  const uploadSubtitle = document.getElementById("upload-subtitle");
  const peptideModeGroup = document.getElementById("peptide-upload-mode-group");
  const complexChainGroup = document.getElementById("complex-chain-group");
  const complexChainHint = document.getElementById("complex-chain-hint");
  const proteinChainChecks = document.getElementById("protein-chain-checks");
  const peptideChainRadios = document.getElementById("peptide-chain-radios");
  const peptideChainCol = document.getElementById("peptide-chain-col");
  const ligandResidueCol = document.getElementById("ligand-residue-col");
  const ligandResidueChecks = document.getElementById("ligand-residue-checks");
  var cachedChains = [];
  var cachedLigandResidues = [];
  var splitPreviewToken = 0;

  const btnSubmit = document.getElementById("btn-submit");
  const submitHint = document.getElementById("submit-hint");
  const progressArea = document.getElementById("progress-area");
  const progressFill = document.getElementById("progress-fill");
  const progressText = document.getElementById("progress-text");
  const taskIdDisplay = document.getElementById("task-id-display");
  const taskQrArea = document.getElementById("task-qr-area");
  const taskQrImg = document.getElementById("task-qr-img");
  const taskStatusLink = document.getElementById("task-status-link");
  const errorArea = document.getElementById("error-area");
  const errorMsg = document.getElementById("error-msg");
  const peptideSeqPanel = document.getElementById("peptide-seq-panel");
  const peptideSeqInput = document.getElementById("peptide-seq-input");
  const peptideSeqHint = document.getElementById("peptide-seq-hint");
  const peptideSeqError = document.getElementById("peptide-seq-error");
  const btnPeptideSeqConfirm = document.getElementById("btn-peptide-seq-confirm");
  var peptideSeqPollInterval = null;
  var peptideSeqTaskId = null;
  const sectionDownload = document.getElementById("section-download");

  const pipeSteps = {
    processing_protein: document.getElementById("pipe-protein"),
    processing_ligand: document.getElementById("pipe-ligand"),
    solvating: document.getElementById("pipe-solvation"),
    converting_gmx: document.getElementById("pipe-convert"),
    generating_mdp: document.getElementById("pipe-mdp"),
  };

  function apiFetch(url, opts) {
    if (window.WebMdAuth && window.WebMdAuth.apiFetch) {
      return window.WebMdAuth.apiFetch(url, opts);
    }
    return fetch(url, opts);
  }

  function showTaskQr(taskId) {
    if (!taskQrArea || !taskId) return;
    var statusUrl = location.origin + "/status.html?id=" + encodeURIComponent(taskId);
    if (taskQrImg) {
      taskQrImg.src =
        "https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=" +
        encodeURIComponent(statusUrl);
    }
    if (taskStatusLink) {
      taskStatusLink.href = statusUrl;
      taskStatusLink.textContent = statusUrl;
    }
    taskQrArea.classList.remove("hidden");
  }

  function hideTaskQr() {
    if (taskQrArea) taskQrArea.classList.add("hidden");
  }

  pdbInput.addEventListener("change", function () {
    pdbName.textContent = this.files[0] ? this.files[0].name : "未选择文件";
    pdbName.classList.toggle("has-file", !!this.files[0]);
    if (isComplexMode()) {
      refreshComplexChains();
    } else {
      updateSubmitButton();
      updatePreview();
    }
  });

  mol2Input.addEventListener("change", function () {
    mol2Name.textContent = this.files[0] ? this.files[0].name : "未选择文件";
    mol2Name.classList.toggle("has-file", !!this.files[0]);
    updateSubmitButton();
    updatePreview();
  });

  if (peptideInput) {
    peptideInput.addEventListener("change", function () {
      if (peptideName) {
        peptideName.textContent = this.files[0] ? this.files[0].name : "未选择文件";
        peptideName.classList.toggle("has-file", !!this.files[0]);
      }
      updateSubmitButton();
      updatePreview();
    });
  }

  function getLigandType() {
    return ligandTypeSel ? ligandTypeSel.value : "mol2";
  }

  function isPeptideMode() {
    var t = getLigandType();
    return t === "cyclic" || t === "linear";
  }

  function isCyclicMode() {
    return getLigandType() === "cyclic";
  }

  function isMol2Mode() {
    return getLigandType() === "mol2";
  }

  function getUploadMode() {
    var el = document.querySelector('input[name="peptide-upload-mode"]:checked');
    return el ? el.value : "separate";
  }

  function isComplexMode() {
    return getUploadMode() === "complex" && (isPeptideMode() || isMol2Mode());
  }

  function getPeptideUploadMode() {
    return getUploadMode();
  }

  function chainKey(ch) {
    return ch === " " || ch === "" ? "_" : ch;
  }

  function parsePdbChains(text) {
    var map = {};
    var lines = text.split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      if (ln.indexOf("ATOM") !== 0 && ln.indexOf("HETATM") !== 0) continue;
      if (ln.length < 26) continue;
      var ch = ln.length > 21 ? ln.charAt(21) : " ";
      var rn = ln.substring(17, 20).trim().toUpperCase();
      var ri = ln.substring(22, 26).trim();
      if (!map[ch]) map[ch] = { chain: ch, residues: {}, n_atoms: 0 };
      map[ch].n_atoms += 1;
      map[ch].residues[ri + "|" + rn] = rn;
    }
    var out = [];
    Object.keys(map).forEach(function (k) {
      var rns = [];
      Object.keys(map[k].residues).forEach(function (rk) {
        rns.push(map[k].residues[rk]);
      });
      out.push({
        chain: map[k].chain,
        label: map[k].chain.trim() ? map[k].chain : "(空白链号)",
        n_residues: rns.length,
        n_atoms: map[k].n_atoms,
        resnames_head: rns.slice(0, 6),
      });
    });
    out.sort(function (a, b) {
      return String(a.label).localeCompare(String(b.label));
    });
    return out;
  }

  var skipLigandRes = {
    HOH: 1, WAT: 1, TIP: 1, TIP3: 1, SOL: 1, DOD: 1, OH2: 1, H2O: 1,
    NA: 1, CL: 1, K: 1, MG: 1, CA: 1, ZN: 1, FE: 1, MN: 1, CO: 1, NI: 1, CU: 1,
    CD: 1, HG: 1, IOD: 1, BR: 1, CS: 1, RB: 1, SR: 1, BA: 1, LI: 1, F: 1,
    "NA+": 1, "CL-": 1, "K+": 1, MG2: 1,
  };

  function ligandResidueKey(ch, rn, ri) {
    var ck = ch === " " || ch === "" ? "_" : ch;
    return ck + "|" + rn + "|" + ri;
  }

  function parsePdbLigandResidues(text) {
    var groups = {};
    var order = [];
    text.split(/\r?\n/).forEach(function (ln) {
      if (ln.indexOf("HETATM") !== 0 || ln.length < 26) return;
      var ch = ln.length > 21 ? ln.charAt(21) : " ";
      var rn = ln.substring(17, 20).trim().toUpperCase();
      var ri = ln.substring(22, 26).trim();
      if (skipLigandRes[rn]) return;
      var gk = ch + "|" + rn + "|" + ri;
      if (!groups[gk]) {
        groups[gk] = {
          chain: ch,
          label: ch.trim() ? ch : "(空白链号)",
          resname: rn,
          resid: ri,
          key: ligandResidueKey(ch, rn, ri),
          n_atoms: 0,
        };
        order.push(gk);
      }
      groups[gk].n_atoms += 1;
    });
    return order.map(function (k) {
      return groups[k];
    });
  }

  function splitComplexMol2ByResidue(text, protKeys, ligKeys) {
    var protSet = {};
    protKeys.forEach(function (k) {
      protSet[k === "_" ? " " : k] = true;
    });
    var ligSet = {};
    ligKeys.forEach(function (k) {
      ligSet[k] = true;
    });
    var protLines = [];
    var ligMap = {};
    ligKeys.forEach(function (k) {
      ligMap[k] = [];
    });
    text.split(/\r?\n/).forEach(function (ln) {
      if (ln.indexOf("ATOM") !== 0 && ln.indexOf("HETATM") !== 0) return;
      if (ln.length < 26) return;
      var ch = ln.charAt(21);
      var rn = ln.substring(17, 20).trim().toUpperCase();
      var ri = ln.substring(22, 26).trim();
      var row = ln;
      if (!/\n$/.test(row)) row += "\n";
      if (ln.indexOf("ATOM") === 0 && protSet[ch]) {
        protLines.push(row);
        return;
      }
      if (ln.indexOf("HETATM") === 0) {
        var lk = ligandResidueKey(ch, rn, ri);
        if (ligSet[lk]) ligMap[lk].push(row);
      }
    });
    var ligParts = [];
    ligKeys.forEach(function (k) {
      if (!ligMap[k] || !ligMap[k].length) {
        throw new Error("按残基拆分配体失败，请检查配体选择");
      }
      var lines = ligMap[k].slice();
      lines.push("END\n");
      ligParts.push(new Blob([lines.join("")], { type: "chemical/x-pdb" }));
    });
    if (!protLines.length) {
      throw new Error("按链拆分蛋白失败，请确认蛋白链选择正确");
    }
    protLines.push("END\n");
    return {
      protein: new Blob([protLines.join("")], { type: "chemical/x-pdb" }),
      ligands: ligParts,
    };
  }

  function splitPdbByChains(text, protKeys, pepKey) {
    var protSet = {};
    protKeys.forEach(function (k) {
      protSet[k === "_" ? " " : k] = true;
    });
    var pepCh = pepKey === "_" ? " " : pepKey;
    var protLines = [];
    var pepLines = [];
    text.split(/\r?\n/).forEach(function (ln) {
      if (ln.indexOf("ATOM") !== 0 && ln.indexOf("HETATM") !== 0) return;
      if (ln.length < 22) return;
      var ch = ln.charAt(21);
      var out = ln;
      if (out.indexOf("HETATM") === 0) out = "ATOM  " + out.slice(6);
      if (!/\n$/.test(out)) out += "\n";
      if (protSet[ch]) protLines.push(out);
      if (ch === pepCh) pepLines.push(out);
    });
    if (!protLines.length || !pepLines.length) {
      throw new Error("按链拆分失败：请确认蛋白链与肽链选择正确");
    }
    protLines.push("END\n");
    pepLines.push("END\n");
    return {
      protein: new Blob([protLines.join("")], { type: "chemical/x-pdb" }),
      peptide: new Blob([pepLines.join("")], { type: "chemical/x-pdb" }),
    };
  }

  function getSelectedProteinChains() {
    if (!proteinChainChecks) return [];
    return Array.prototype.map
      .call(proteinChainChecks.querySelectorAll('input[type="checkbox"]:checked'), function (el) {
        return el.value;
      });
  }

  function getSelectedPeptideChain() {
    if (!peptideChainRadios) return "";
    var el = peptideChainRadios.querySelector('input[type="radio"]:checked');
    return el ? el.value : "";
  }

  function getSelectedLigandResidues() {
    if (!ligandResidueChecks) return [];
    return Array.prototype.map
      .call(ligandResidueChecks.querySelectorAll('input[type="checkbox"]:checked'), function (el) {
        return el.value;
      });
  }

  function renderLigandResiduePickers(residues) {
    cachedLigandResidues = residues || [];
    if (!ligandResidueChecks) return;
    if (!cachedLigandResidues.length) {
      ligandResidueChecks.innerHTML = "<p class=\"hint\">未识别到 HETATM 配体残基（已排除水/离子）</p>";
      return;
    }
    var sorted = cachedLigandResidues.slice().sort(function (a, b) {
      return a.n_atoms - b.n_atoms;
    });
    var defaultPick = sorted[0];
    ligandResidueChecks.innerHTML = cachedLigandResidues
      .map(function (r) {
        var checked = r.key === defaultPick.key;
        return (
          '<label class="chain-option"><input type="checkbox" name="ligand-residue" value="' +
          r.key +
          '"' +
          (checked ? " checked" : "") +
          '><span class="chain-option-text">链 <code>' +
          r.label +
          "</code> · <code>" +
          r.resname +
          r.resid +
          "</code> · " +
          r.n_atoms +
          " 原子</span></label>"
        );
      })
      .join("");
    ligandResidueChecks.querySelectorAll("input").forEach(function (el) {
      el.addEventListener("change", function () {
        var picked = getSelectedLigandResidues();
        if (picked.length > 3) {
          el.checked = false;
          showError("最多选择 3 个配体残基");
        }
        updateSubmitButton();
        updatePreview();
      });
    });
  }

  function renderChainPickers(chains) {
    cachedChains = chains || [];
    if (!proteinChainChecks || !peptideChainRadios) return;
    if (!cachedChains.length) {
      proteinChainChecks.innerHTML = "<p class=\"hint\">未识别到链，请检查 PDB</p>";
      peptideChainRadios.innerHTML = "";
      return;
    }
    // 默认：残基数最多的为蛋白，最少的为肽（可改）
    var sorted = cachedChains.slice().sort(function (a, b) {
      return b.n_residues - a.n_residues;
    });
    var defaultPep = sorted[sorted.length - 1];
    var defaultProt = sorted.filter(function (c) {
      return c.chain !== defaultPep.chain;
    });
    if (!defaultProt.length) defaultProt = [sorted[0]];

    proteinChainChecks.innerHTML = cachedChains
      .map(function (c) {
        var key = chainKey(c.chain);
        var checked = defaultProt.some(function (p) {
          return p.chain === c.chain;
        });
        var seq =
          c.resnames_head && c.resnames_head.length
            ? " · " + c.resnames_head.join("-")
            : "";
        return (
          '<label class="chain-option"><input type="checkbox" name="protein-chain" value="' +
          key +
          '"' +
          (checked ? " checked" : "") +
          '><span class="chain-option-text">链 <code>' +
          c.label +
          "</code> · " +
          c.n_residues +
          " 残基 / " +
          c.n_atoms +
          " 原子" +
          seq +
          "</span></label>"
        );
      })
      .join("");

    peptideChainRadios.innerHTML = cachedChains
      .map(function (c) {
        var key = chainKey(c.chain);
        var checked = c.chain === defaultPep.chain;
        return (
          '<label class="chain-option"><input type="radio" name="peptide-chain" value="' +
          key +
          '"' +
          (checked ? " checked" : "") +
          '><span class="chain-option-text">链 <code>' +
          c.label +
          "</code> · " +
          c.n_residues +
          " 残基 / " +
          c.n_atoms +
          " 原子</span></label>"
        );
      })
      .join("");

    proteinChainChecks.querySelectorAll("input").forEach(function (el) {
      el.addEventListener("change", function () {
        updateSubmitButton();
        updatePreview();
      });
    });
    peptideChainRadios.querySelectorAll("input").forEach(function (el) {
      el.addEventListener("change", function () {
        updateSubmitButton();
        updatePreview();
      });
    });
  }

  function refreshComplexChains() {
    var f = pdbInput && pdbInput.files[0];
    if (!f) {
      renderChainPickers([]);
      renderLigandResiduePickers([]);
      updateSubmitButton();
      updatePreview();
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var text = String(reader.result || "");
        renderChainPickers(parsePdbChains(text));
        if (isMol2Mode()) {
          renderLigandResiduePickers(parsePdbLigandResidues(text));
        } else {
          renderLigandResiduePickers([]);
        }
      } catch (e) {
        renderChainPickers([]);
        renderLigandResiduePickers([]);
        showError(e.message || "解析复合物失败");
      }
      updateSubmitButton();
      updatePreview();
    };
    reader.onerror = function () {
      showError("读取复合物 PDB 失败");
      updateSubmitButton();
    };
    reader.readAsText(f);
  }

  function syncLigandTypeUi() {
    var t = getLigandType();
    var pep = isPeptideMode();
    var mol2 = isMol2Mode();
    var complexMode = isComplexMode();
    if (mol2Group) mol2Group.classList.toggle("hidden", pep || complexMode);
    if (peptideModeGroup) peptideModeGroup.classList.toggle("hidden", !(pep || mol2));
    if (peptideGroup) peptideGroup.classList.toggle("hidden", !pep || complexMode);
    if (complexChainGroup) complexChainGroup.classList.toggle("hidden", !complexMode);
    if (mol2Actions) mol2Actions.classList.toggle("hidden", pep || complexMode);
    if (peptideChainCol) peptideChainCol.classList.toggle("hidden", !pep || !complexMode);
    if (ligandResidueCol) ligandResidueCol.classList.toggle("hidden", !mol2 || !complexMode);
    if (peptideLabel) {
      peptideLabel.textContent = t === "linear" ? "线形肽 · PDB" : "环肽 · PDB";
    }
    if (pdbFileLabel) {
      pdbFileLabel.textContent = complexMode ? "复合物 PDB 文件" : "蛋白 PDB 文件";
    }
    if (complexChainHint) {
      if (mol2 && complexMode) {
        complexChainHint.textContent =
          "上传对接/复合物 PDB 后：勾选蛋白链（ATOM），并选择 HETATM 配体残基（最多 3 个；已排除水/离子）。";
      } else if (pep && complexMode) {
        complexChainHint.textContent =
          "上传复合物后将列出链；请勾选蛋白链，并选择一条肽链。";
      }
    }
    if (uploadSubtitle) {
      if (mol2 && complexMode) {
        uploadSubtitle.textContent =
          "上传蛋白-配体复合物 PDB，选择蛋白链与配体残基（坐标已固定，服务端自动转 MOL2）";
      } else if (pep && complexMode) {
        uploadSubtitle.textContent =
          "上传蛋白+肽复合物 PDB，选择蛋白链与肽链（标准氨基酸；相对位置已固定在同一文件中）";
      } else if (t === "cyclic") {
        uploadSubtitle.textContent =
          "蛋白 PDB + 环肽 PDB（标准氨基酸，头尾成环；请预先摆好相对位置）";
      } else if (t === "linear") {
        uploadSubtitle.textContent =
          "蛋白 PDB + 线形肽 PDB（标准氨基酸，保留 N/C 末端；请预先摆好相对位置）";
      } else {
        uploadSubtitle.textContent =
          "蛋白 PDB 必填；配体 MOL2 至少 1 个，最多 3 个（请在外部软件中摆好相对位置）";
      }
    }
    if (ligandTypeHint) {
      if (t === "cyclic") {
        ligandTypeHint.textContent =
          "环肽可用分开上传或复合物按链拆分。ff14SB + 自动连接首尾 N–C；暂不支持侧链杂原子修饰。";
      } else if (t === "linear") {
        ligandTypeHint.textContent =
          "线形肽可用分开上传或复合物按链拆分。ff14SB，保留 N/C 末端；蛋白将自动补全链内部缺失残基。";
      } else {
        ligandTypeHint.textContent =
          "小分子可分开上传 MOL2，或上传复合物 PDB 后选择蛋白链与 HETATM 配体残基（自动转 MOL2）。";
      }
    }
    var addH = document.getElementById("ligand-add-h");
    if (addH) {
      addH.disabled = pep;
      if (pep) addH.checked = false;
    }
    if (complexMode && pdbInput && pdbInput.files[0]) {
      refreshComplexChains();
    } else {
      updateSubmitButton();
      updatePreview();
    }
  }

  if (ligandTypeSel) {
    ligandTypeSel.addEventListener("change", syncLigandTypeUi);
  }
  document.querySelectorAll('input[name="peptide-upload-mode"]').forEach(function (el) {
    el.addEventListener("change", syncLigandTypeUi);
  });

  window.WebMD = window.WebMD || {};
  window.WebMD.onLigandChange = function () {
    updateSubmitButton();
    updatePreview();
  };

  function getMol2Files() {
    if (window.WebMD && window.WebMD.getMol2Files) {
      return window.WebMD.getMol2Files();
    }
    return mol2Input.files[0] ? [mol2Input.files[0]] : [];
  }

  function updatePreview() {
    if (!window.MdViewer) return;
    if (isPeptideMode()) {
      if (isComplexMode()) {
        var f = pdbInput.files[0];
        var pChains = getSelectedProteinChains();
        var pepCh = getSelectedPeptideChain();
        if (!f || !pChains.length || !pepCh || !window.MdViewer.loadFromPdbs) {
          window.MdViewer.hide();
          return;
        }
        var token = ++splitPreviewToken;
        var reader = new FileReader();
        reader.onload = function () {
          if (token !== splitPreviewToken) return;
          try {
            var parts = splitPdbByChains(String(reader.result || ""), pChains, pepCh);
            var protFile = new File([parts.protein], "protein_split.pdb", {
              type: "chemical/x-pdb",
            });
            var pepFile = new File([parts.peptide], "peptide_split.pdb", {
              type: "chemical/x-pdb",
            });
            window.MdViewer.loadFromPdbs(protFile, pepFile);
          } catch (e) {
            window.MdViewer.hide();
          }
        };
        reader.readAsText(f);
        return;
      }
      if (
        pdbInput.files[0] &&
        peptideInput &&
        peptideInput.files[0] &&
        window.MdViewer.loadFromPdbs
      ) {
        window.MdViewer.loadFromPdbs(pdbInput.files[0], peptideInput.files[0]);
      } else {
        window.MdViewer.hide();
      }
      return;
    }
    if (isMol2Mode() && isComplexMode()) {
      var f2 = pdbInput.files[0];
      var pChains2 = getSelectedProteinChains();
      var ligKeys = getSelectedLigandResidues();
      if (!f2 || !pChains2.length || !ligKeys.length || !window.MdViewer.loadFromPdbs) {
        window.MdViewer.hide();
        return;
      }
      var token2 = ++splitPreviewToken;
      var reader2 = new FileReader();
      reader2.onload = function () {
        if (token2 !== splitPreviewToken) return;
        try {
          var parts2 = splitComplexMol2ByResidue(String(reader2.result || ""), pChains2, ligKeys);
          var protFile2 = new File([parts2.protein], "protein_split.pdb", {
            type: "chemical/x-pdb",
          });
          var ligFile2 = new File([parts2.ligands[0]], "ligand_split.pdb", {
            type: "chemical/x-pdb",
          });
          window.MdViewer.loadFromPdbs(protFile2, ligFile2);
        } catch (e) {
          window.MdViewer.hide();
        }
      };
      reader2.readAsText(f2);
      return;
    }
    var mol2s = getMol2Files();
    if (pdbInput.files[0] && mol2s.length && window.MdViewer.loadFromFiles) {
      window.MdViewer.loadFromFiles(pdbInput.files[0], mol2s);
    } else {
      window.MdViewer.hide();
    }
  }

  function updateSubmitButton() {
    var ready;
    if (isPeptideMode()) {
      if (isComplexMode()) {
        var pcs = getSelectedProteinChains();
        var pep = getSelectedPeptideChain();
        ready = !!(
          pdbInput.files[0] &&
          pcs.length &&
          pep &&
          pcs.indexOf(pep) < 0
        );
      } else {
        ready = !!(pdbInput.files[0] && peptideInput && peptideInput.files[0]);
      }
    } else if (isMol2Mode() && isComplexMode()) {
      var pcs2 = getSelectedProteinChains();
      var ligs = getSelectedLigandResidues();
      ready = !!(pdbInput.files[0] && pcs2.length && ligs.length && ligs.length <= 3);
    } else {
      ready = !!(pdbInput.files[0] && getMol2Files().length > 0);
    }
    var loggedIn = window.WebMdAuth && window.WebMdAuth.getToken();
    btnSubmit.disabled = !ready;
    if (!loggedIn && ready) {
      submitHint.textContent = "请先登录后再提交";
    } else if (ready) {
      submitHint.textContent = "文件已就绪，点击提交";
    } else if (isPeptideMode() && isComplexMode()) {
      submitHint.textContent = "请上传复合物 PDB，并选择蛋白链与肽链（不可重复）";
    } else if (isMol2Mode() && isComplexMode()) {
      submitHint.textContent = "请上传复合物 PDB，并选择蛋白链与配体残基（1–3 个）";
    } else if (getLigandType() === "cyclic") {
      submitHint.textContent = "请先上传蛋白 PDB 与环肽 PDB";
    } else if (getLigandType() === "linear") {
      submitHint.textContent = "请先上传蛋白 PDB 与线形肽 PDB";
    } else {
      submitHint.textContent = "请先上传 PDB 与至少一个 MOL2";
    }
  }

  btnSubmit.disabled = true;

  function getParams() {
    return {
      temperature: parseFloat(document.getElementById("temperature").value),
      pressure: parseFloat(document.getElementById("pressure").value),
      timestep: parseFloat(document.getElementById("timestep").value) / 1000,
      simulation_time_ns: parseFloat(document.getElementById("sim-time").value),
      ion_conc: parseFloat(document.getElementById("ion-conc").value),
      salt_type: document.getElementById("salt-type")
        ? document.getElementById("salt-type").value
        : "nacl",
      constraints: document.getElementById("constraints").value,
      nonbonded_cutoff: parseFloat(document.getElementById("nonbonded-cutoff").value),
      box_padding: parseFloat(document.getElementById("box-padding").value),
      tau_t: parseFloat(document.getElementById("tau-t").value),
      tau_p: parseFloat(document.getElementById("tau-p").value),
      report_interval_ps: parseFloat(document.getElementById("report-interval").value),
      ligand_add_hydrogens: document.getElementById("ligand-add-h") &&
        document.getElementById("ligand-add-h").checked ? "1" : "0",
      ligand_type: getLigandType(),
      is_cyclic_peptide: getLigandType() === "cyclic" ? "1" : "0",
      is_linear_peptide: getLigandType() === "linear" ? "1" : "0",
      peptide_upload_mode: (isPeptideMode() || isMol2Mode()) ? getUploadMode() : "separate",
      protein_chains: "",
      peptide_chain: "",
      ligand_residues: "",
    };
  }

  btnSubmit.addEventListener("click", async function () {
    if (isPeptideMode()) {
      if (isComplexMode()) {
        if (!pdbInput.files[0] || !getSelectedProteinChains().length || !getSelectedPeptideChain()) {
          return;
        }
        if (getSelectedProteinChains().indexOf(getSelectedPeptideChain()) >= 0) {
          showError("肽链不能与蛋白链重复，请重新选择");
          return;
        }
      } else if (!pdbInput.files[0] || !peptideInput || !peptideInput.files[0]) {
        return;
      }
    } else if (isMol2Mode() && isComplexMode()) {
      if (
        !pdbInput.files[0] ||
        !getSelectedProteinChains().length ||
        !getSelectedLigandResidues().length
      ) {
        return;
      }
    } else if (!pdbInput.files[0] || !getMol2Files().length) {
      return;
    }
    if (window.WebMdAuth && !window.WebMdAuth.requireLogin()) return;

    var params = getParams();
    if (params.simulation_time_ns !== 10 && params.simulation_time_ns !== 100 && params.simulation_time_ns !== 200) {
      showError("模拟时长仅支持 10 ns、100 ns 或 200 ns");
      return;
    }
    if (isPeptideMode() && isComplexMode()) {
      params.protein_chains = getSelectedProteinChains().join(",");
      params.peptide_chain = getSelectedPeptideChain();
    }
    if (isMol2Mode() && isComplexMode()) {
      params.protein_chains = getSelectedProteinChains().join(",");
      params.ligand_residues = getSelectedLigandResidues().join(",");
    }

    errorArea.classList.add("hidden");
    sectionDownload.classList.add("hidden");
    hideTaskQr();
    if (window.PaymentUI) window.PaymentUI.reset();
    progressArea.classList.remove("hidden");
    resetPipeline();

    var formData = new FormData();
    formData.append("pdb_file", pdbInput.files[0]);
    if (isPeptideMode()) {
      if (getUploadMode() === "separate") {
        formData.append("cyclic_peptide_file", peptideInput.files[0]);
      }
    } else if (!isComplexMode()) {
      if (window.WebMD && window.WebMD.appendMol2ToFormData) {
        window.WebMD.appendMol2ToFormData(formData);
      } else {
        formData.append("mol2_file", mol2Input.files[0]);
      }
    }
    Object.keys(params).forEach(function (key) {
      formData.append(key, params[key]);
    });

    try {
      btnSubmit.disabled = true;
      btnSubmit.textContent = "提交中...";

      var resp = await apiFetch("/api/tasks", { method: "POST", body: formData });

      if (!resp.ok) {
        var err = await resp.json();
        var detail = err.detail;
        if (Array.isArray(detail)) {
          detail = detail.map(function (x) { return x.msg || JSON.stringify(x); }).join("; ");
        }
        throw new Error(detail || "创建任务失败");
      }

      var task = await resp.json();
      // Job ID 可点击进入状态页；并提供「我的任务」入口
      if (taskIdDisplay) {
        var tid = task.task_id;
        taskIdDisplay.innerHTML =
          'Job ID：<a class="task-id-link" href="/status.html?id='
          + encodeURIComponent(tid)
          + '" target="_blank" rel="noopener"><code>'
          + tid
          + "</code></a>"
          + ' · <a class="task-id-link" href="/jobs.html">我的任务</a>';
      }
      showTaskQr(task.task_id);
      pollTask(task.task_id);
    } catch (e) {
      showError(e.message);
      btnSubmit.disabled = false;
      btnSubmit.textContent = "开始准备模拟体系";
    }
  });

  function hidePeptideSeqPanel() {
    if (peptideSeqPanel) peptideSeqPanel.classList.add("hidden");
    if (peptideSeqError) peptideSeqError.textContent = "";
  }

  function showPeptideSeqPanel(task) {
    if (!peptideSeqPanel) return;
    peptideSeqPanel.classList.remove("hidden");
    peptideSeqTaskId = task.task_id;
    var hint = "检测到非标准肽 PDB（常见于对接导出）。请输入单字母序列；仅当组成与三维匹配全部通过后才会继续。";
    if (task.peptide_sequence_hint_n) {
      hint += " 结构中氮原子约 " + task.peptide_sequence_hint_n + " 个，可作长度参考。";
    }
    if (task.error_message) {
      hint += " " + task.error_message;
    }
    if (peptideSeqHint) peptideSeqHint.textContent = hint;
    if (peptideSeqError) peptideSeqError.textContent = "";
  }

  function pollTask(taskId) {
    if (peptideSeqPollInterval) {
      clearInterval(peptideSeqPollInterval);
      peptideSeqPollInterval = null;
    }
    peptideSeqPollInterval = setInterval(async function () {
      try {
        var resp = await apiFetch("/api/tasks/" + taskId);
        if (!resp.ok) {
          clearInterval(peptideSeqPollInterval);
          peptideSeqPollInterval = null;
          return;
        }

        var task = await resp.json();
        updatePipeline(task.status);
        progressText.textContent = task.status_label;

        var progressMap = {
          pending: 5,
          processing_protein: 15,
          processing_ligand: 30,
          awaiting_peptide_sequence: 35,
          solvating: 50,
          converting_gmx: 70,
          generating_mdp: 85,
          packaging: 95,
          completed: 100,
        };
        progressFill.style.width = (progressMap[task.status] || 0) + "%";

        if (task.status === "awaiting_peptide_sequence") {
          showPeptideSeqPanel(task);
          return;
        }
        hidePeptideSeqPanel();

        if (task.status === "completed") {
          clearInterval(peptideSeqPollInterval);
          peptideSeqPollInterval = null;
          sectionDownload.classList.remove("hidden");
          renderLigandFfSummary(task);
          if (window.MdViewer && (!window.MdViewer.hasLocalPreview || !window.MdViewer.hasLocalPreview())) {
            window.MdViewer.load(taskId);
          }
          if (window.PaymentUI) window.PaymentUI.onTaskReady(taskId);
          btnSubmit.textContent = "重新提交";
          btnSubmit.disabled = false;
        }

        if (task.status === "failed") {
          clearInterval(peptideSeqPollInterval);
          peptideSeqPollInterval = null;
          showError(task.error_message || "任务执行失败");
          btnSubmit.textContent = "重新提交";
          btnSubmit.disabled = false;
        }
      } catch (e) {
        clearInterval(peptideSeqPollInterval);
        peptideSeqPollInterval = null;
        showError("轮询失败: " + e.message);
        btnSubmit.disabled = false;
        btnSubmit.textContent = "开始准备模拟体系";
      }
    }, 1500);
  }

  if (btnPeptideSeqConfirm) {
    btnPeptideSeqConfirm.addEventListener("click", async function () {
      if (!peptideSeqTaskId || !peptideSeqInput) return;
      var seq = (peptideSeqInput.value || "").trim();
      if (peptideSeqError) peptideSeqError.textContent = "";
      btnPeptideSeqConfirm.disabled = true;
      btnPeptideSeqConfirm.textContent = "核实中…";
      try {
        var resp = await apiFetch("/api/tasks/" + peptideSeqTaskId + "/peptide-sequence", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sequence: seq }),
        });
        var data = await resp.json().catch(function () { return {}; });
        if (!resp.ok) {
          throw new Error(data.detail || "序列确认失败");
        }
        hidePeptideSeqPanel();
        progressText.textContent = "序列已提交，继续处理…";
        pollTask(peptideSeqTaskId);
      } catch (e) {
        if (peptideSeqError) peptideSeqError.textContent = e.message || "确认失败";
      } finally {
        btnPeptideSeqConfirm.disabled = false;
        btnPeptideSeqConfirm.textContent = "确认并继续";
      }
    });
  }

  function resetPipeline() {
    Object.keys(pipeSteps).forEach(function (k) {
      var el = pipeSteps[k];
      if (el) {
        el.classList.remove("active", "done", "failed");
      }
    });
  }

  function updatePipeline(status) {
    resetPipeline();
    var order = [
      "processing_protein", "processing_ligand", "solvating",
      "converting_gmx", "generating_mdp",
    ];
    // 等待肽序列时仍停留在配体/肽步骤
    if (status === "awaiting_peptide_sequence") {
      status = "processing_ligand";
    }
    var found = false;
    order.forEach(function (key) {
      if (found) return;
      var el = pipeSteps[key];
      if (!el) return;
      if (key === status) {
        el.classList.add("active");
        found = true;
      } else {
        el.classList.add("done");
      }
    });

    if (status === "completed") {
      Object.keys(pipeSteps).forEach(function (k) {
        var el = pipeSteps[k];
        if (el) el.classList.add("done");
      });
    }
    if (status === "failed") {
      Object.keys(pipeSteps).forEach(function (k) {
        var el = pipeSteps[k];
        if (el && el.classList.contains("active")) {
          el.classList.remove("active");
          el.classList.add("failed");
        }
      });
    }
  }

  function showError(msg) {
    errorArea.classList.remove("hidden");
    errorMsg.textContent = msg;
  }

  function renderLigandFfSummary(task) {
    var box = document.getElementById("ligand-ff-summary");
    if (!box) return;
    var ligs = task.ligands_ff || (task.params && task.params.ligands) || [];
    var lt = (task.params && task.params.ligand_type) || "";
    var isCyc =
      lt === "cyclic" || !!(task.params && task.params.is_cyclic_peptide);
    var isLin =
      lt === "linear" || !!(task.params && task.params.is_linear_peptide);
    if (!ligs.length || isCyc || isLin) {
      if (isCyc) {
        box.innerHTML =
          "<h4>力场说明</h4><p>环肽：Amber <strong>ff14SB</strong>（头尾 N–C 成环）；蛋白同为 ff14SB。</p>";
        box.classList.remove("hidden");
      } else if (isLin) {
        box.innerHTML =
          "<h4>力场说明</h4><p>线形肽：Amber <strong>ff14SB</strong>（保留 N/C 末端，不成环）；蛋白同为 ff14SB。</p>";
        box.classList.remove("hidden");
      } else {
        box.classList.add("hidden");
      }
      return;
    }
    var items = ligs.map(function (L) {
      var nc = L.net_charge != null ? L.net_charge : "—";
      return (
        "<li><strong>" +
        (L.resname || "LIG") +
        "</strong>（" +
        (L.source || "") +
        "）：净电荷 <strong>" +
        nc +
        "</strong> · 力场 " +
        (L.force_field || "GAFF2") +
        " · 电荷方法 " +
        (L.charge_method || "AM1-BCC") +
        "</li>"
      );
    });
    box.innerHTML =
      "<h4>小分子力场参数</h4><ul>" +
      items.join("") +
      "</ul><p class=\"hint\">以上设置已写入结果包中的 ligand_forcefield_summary.json</p>";
    box.classList.remove("hidden");
  }

  window.addEventListener("storage", updateSubmitButton);
  setInterval(updateSubmitButton, 2000);
  syncLigandTypeUi();
})();
