/**
 * 解析 PDBQT / 多 MODEL PDB 中的对接构象，供预览与提交索引使用。
 */
(function (global) {
  "use strict";

  /** 对接树等非坐标行，导出为 PDB 预览时丢弃 */
  var SKIP_PREFIXES = [
    "ROOT",
    "ENDROOT",
    "BRANCH",
    "ENDBRANCH",
    "TORSDOF",
    "BEGIN_RES",
    "END_RES",
  ];

  function _shouldSkip(ln) {
    var s = (ln || "").trim();
    if (!s) return true;
    for (var i = 0; i < SKIP_PREFIXES.length; i++) {
      if (s.indexOf(SKIP_PREFIXES[i]) === 0) return true;
    }
    return false;
  }

  /**
   * 将一块构象文本转为可给 NGL 的 PDB 字符串（仅 ATOM/HETATM/TER/END）。
   */
  function poseBlockToPdb(block) {
    var lines = String(block || "").split(/\r?\n/);
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      if (!ln) continue;
      var tag = ln.length >= 6 ? ln.slice(0, 6).trim() : ln.trim();
      if (tag === "MODEL" || tag === "ENDMDL") continue;
      if (_shouldSkip(ln)) continue;
      if (
        ln.indexOf("ATOM") === 0 ||
        ln.indexOf("HETATM") === 0 ||
        ln.indexOf("TER") === 0
      ) {
        // PDBQT 电荷/类型在 55 列之后；截到标准 PDB 坐标区即可
        if (ln.indexOf("ATOM") === 0 || ln.indexOf("HETATM") === 0) {
          out.push(ln.length > 66 ? ln.slice(0, 66) : ln);
        } else {
          out.push(ln);
        }
      }
    }
    if (!out.length) return "";
    out.push("END");
    return out.join("\n") + "\n";
  }

  /**
   * 解析 PDBQT 文本为构象列表。
   * @returns {{ count: number, poses: string[] }} poses 为 PDB 文本
   */
  function parsePdbqtPoses(text) {
    var raw = String(text || "");
    var lines = raw.split(/\r?\n/);
    var blocks = [];
    var cur = [];
    var inModel = false;
    var sawModel = false;

    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      var head = ln.length >= 5 ? ln.slice(0, 5).trim() : ln.trim();
      if (head === "MODEL") {
        sawModel = true;
        if (cur.length) {
          blocks.push(cur.join("\n"));
          cur = [];
        }
        inModel = true;
        continue;
      }
      if (head === "ENDMDL") {
        if (cur.length) {
          blocks.push(cur.join("\n"));
          cur = [];
        }
        inModel = false;
        continue;
      }
      if (sawModel && !inModel) continue;
      cur.push(ln);
    }
    if (cur.length) {
      blocks.push(cur.join("\n"));
    }

    var poses = [];
    for (var j = 0; j < blocks.length; j++) {
      var pdb = poseBlockToPdb(blocks[j]);
      if (pdb) poses.push(pdb);
    }

    // 无 MODEL 且整文件可解析为单构象
    if (!poses.length) {
      var one = poseBlockToPdb(raw);
      if (one) poses.push(one);
    }

    return { count: poses.length, poses: poses };
  }

  global.WebMdPdbqt = {
    parsePdbqtPoses: parsePdbqtPoses,
    poseBlockToPdb: poseBlockToPdb,
  };
})(typeof window !== "undefined" ? window : this);
