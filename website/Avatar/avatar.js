import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { GLTFExporter } from "three/addons/exporters/GLTFExporter.js";
import { STLExporter } from "three/addons/exporters/STLExporter.js";
import * as SkeletonUtils from "three/addons/utils/SkeletonUtils.js";

(function () {
  "use strict";

  const CONFIG_URL = "../data.config.json";
  const AVATAR_INDEX_URL = "../avatar-index.json";
  const ANIMATION_INDEX_URL = "../animation-index.json";
  const DEFAULT_CONFIG = {
    repoOwner: "RSDWArchive",
    repoName: "RSDWModel",
    repoBranch: "main",
    datasetVersion: "0.11.2.2",
    assetBaseUrl: "auto",
  };
  const SLOT_ORDER = ["baseBody", "baseHead", "hair", "beard", "torso", "legs", "helmet", "cape", "rightHand", "leftHand"];
  const OPTIONAL_SLOTS = new Set(["hair", "beard", "torso", "legs", "helmet", "cape", "rightHand", "leftHand"]);
  const HAND_SLOTS = ["rightHand", "leftHand"];
  const HELD_SLOTS = new Set(HAND_SLOTS);
  const ATTACHMENT_SOURCE_SLOTS = ["baseBody", "torso", "legs", "baseHead"];
  const SLOT_LABELS = {
    baseBody: "Body",
    baseHead: "Head",
    rightHand: "Right hand",
    leftHand: "Left hand",
  };
  const GAME_START_DEFAULTS = {
    sex: "M_MED",
    slots: {
      baseBody: "SK:RSDragonwilds/Content/Art/Skeleton/Player/Body/M_MED_Body_A_01/SK_M_MED_Body_A_01.uemodel",
      baseHead: "SK:RSDragonwilds/Content/Art/Skeleton/Player/Heads/M_MED_Head_A_01/SK_M_MED_Head_A_01.uemodel",
      torso: "SK:RSDragonwilds/Content/Art/Skeleton/Armour/M_MED/LightArmour_01/SK_M_MED_Body_LightArmour_01.uemodel",
      legs: "SK:RSDragonwilds/Content/Art/Skeleton/Armour/M_MED/LightArmour_01/SK_M_MED_Legs_LightArmour_01.uemodel",
    },
    colors: {
      skin: "skin08",
      hair: "hair09",
      eyes: "eye08",
    },
  };
  const COLOR_ROLES = ["skin", "hair", "eyes"];
  const COLOR_HASH_KEYS = {
    skin: "skinColor",
    hair: "hairColor",
    eyes: "eyeColor",
  };
  const ANIMATION_CAPTURE_TARGET_FPS = 30;
  const ANIMATION_CAPTURE_MAX_FRAMES = 450;
  const ANIMATION_CAPTURE_QUALITY = 0.82;
  const DEFAULT_ATTACH_FALLBACKS = {
    rightHand: ["prop_r", "hand_r"],
    leftHand: ["prop_l", "hand_l"],
  };
  const DEFAULT_HELD_ROTATION = {
    x: Math.PI / 2,
    y: 0,
    z: 0,
  };
  const SHIELD_HELD_ROTATION = {
    x: Math.PI / 2,
    y: 0,
    z: Math.PI,
  };
  const SHIELD_HELD_OFFSET = {
    x: 0.08,
    y: 0.05,
    z: 0,
  };
  const BOW_HELD_ROTATION = {
    x: Math.PI / 2,
    y: 0,
    z: Math.PI,
  };
  const BOW_HELD_OFFSET = {
    x: 0.08,
    y: 0.05,
    z: 0,
  };
  const ZERO_VECTOR = { x: 0, y: 0, z: 0 };

  const els = {
    stage: document.getElementById("avatar-stage"),
    loading: document.getElementById("avatar-loading"),
    status: document.getElementById("avatar-status"),
    warning: document.getElementById("avatar-warning"),
    summary: document.getElementById("avatar-summary"),
    resetView: document.getElementById("reset-view"),
    screenshot: document.getElementById("avatar-screenshot"),
    download: document.getElementById("avatar-download"),
    downloadDialog: document.getElementById("avatar-download-dialog"),
    downloadBackdrop: document.getElementById("avatar-download-dialog-backdrop"),
    downloadClose: document.getElementById("avatar-download-dialog-close"),
    downloadGlb: document.getElementById("avatar-download-glb"),
    downloadStl: document.getElementById("avatar-download-stl"),
    downloadStatus: document.getElementById("avatar-download-status"),
    copyLink: document.getElementById("copy-avatar-link"),
    openModel: document.getElementById("open-current-model"),
    sexM: document.getElementById("sex-m"),
    sexF: document.getElementById("sex-f"),
    bodyVisible: document.getElementById("body-visible"),
    headVisible: document.getElementById("head-visible"),
    animationFilter: document.getElementById("avatar-animation-filter"),
    animationSelect: document.getElementById("avatar-animation-select"),
    animationPlay: document.getElementById("avatar-animation-play"),
    animationCapture: document.getElementById("avatar-animation-capture"),
    slotControls: Object.fromEntries(SLOT_ORDER.map((slot) => [slot, document.getElementById(`slot-${slot}`)])),
    handAdjustPanels: Object.fromEntries(HAND_SLOTS.map((slot) => [slot, document.querySelector(`[data-hand-adjust="${slot}"]`)])),
    handAdjustInputs: Array.from(document.querySelectorAll("[data-hand-adjust-input]")),
    handAdjustReset: Array.from(document.querySelectorAll("[data-hand-adjust-reset]")),
    swatches: {
      skin: document.getElementById("skin-swatches"),
      hair: document.getElementById("hair-swatches"),
      eyes: document.getElementById("eye-swatches"),
    },
  };

  let config = { ...DEFAULT_CONFIG };
  let avatarIndex = null;
  let animationIndex = null;
  let state = null;
  let activeSlot = "baseHead";
  let renderer = null;
  let scene = null;
  let camera = null;
  let controls = null;
  let avatarRoot = null;
  let loader = null;
  let hasFitCamera = false;
  let clock = null;
  let animationPlaying = true;
  let activeAnimationDuration = 0;
  let isCapturingAnimation = false;
  let isExportingAvatar = false;
  let animationFilterText = "";
  const gltfCache = new Map();
  const textureCache = new Map();
  const activeObjects = new Map();
  const activeMixers = [];
  const textureLoader = new THREE.TextureLoader();
  textureLoader.setCrossOrigin("anonymous");

  function isLocalHost() {
    return ["localhost", "127.0.0.1", ""].includes(window.location.hostname);
  }

  function trimSlash(value) {
    return String(value || "").replace(/\/+$/, "");
  }

  function encodePath(path) {
    return String(path || "").split("/").map(encodeURIComponent).join("/");
  }

  function rawRepoBase() {
    return `https://raw.githubusercontent.com/${config.repoOwner}/${config.repoName}/${config.repoBranch}`;
  }

  function githubRepoBase() {
    return `https://github.com/${config.repoOwner}/${config.repoName}/tree/${config.repoBranch}`;
  }

  function webAssetBase() {
    if (config.assetBaseUrl && config.assetBaseUrl !== "auto") {
      return trimSlash(config.assetBaseUrl);
    }
    if (isLocalHost()) {
      return `../../${encodePath(config.datasetVersion)}/WebAssets`;
    }
    return `${rawRepoBase()}/${encodePath(config.datasetVersion)}/WebAssets`;
  }

  function assetUrl(relPath) {
    return `${webAssetBase()}/${encodePath(relPath)}`;
  }

  function githubModelUrl(model) {
    return `${githubRepoBase()}/${encodePath(config.datasetVersion)}/WebAssets/${encodePath(model.gltfPath)}`;
  }

  function avatarAnimationFileName() {
    const row = animationById(state.animation);
    const animationName = row ? (row.label || row.name || row.id) : "Animation";
    const baseName = `RSDWModel_Avatar_${state.sex}_${animationName}`
      .replace(/[^a-z0-9_-]+/gi, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 96);
    return `${baseName || "RSDWModel_Avatar"}_animation.webp`;
  }

  function avatarExportBaseName() {
    const row = animationById(state.animation);
    const animationName = row ? (row.label || row.name || row.id) : "";
    const baseName = `RSDWModel_Avatar_${state.sex}_${animationName}`
      .replace(/[^a-z0-9_-]+/gi, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 96);
    return baseName || "RSDWModel_Avatar";
  }

  function downloadBlob(blob, fileName) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  function dataUrlToBytes(dataUrl) {
    const comma = dataUrl.indexOf(",");
    if (comma < 0) throw new Error("Invalid data URL.");
    const binary = window.atob(dataUrl.slice(comma + 1));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  function asciiBytes(value) {
    const bytes = new Uint8Array(value.length);
    for (let i = 0; i < value.length; i += 1) bytes[i] = value.charCodeAt(i);
    return bytes;
  }

  function writeUint24(bytes, offset, value) {
    bytes[offset] = value & 0xff;
    bytes[offset + 1] = (value >> 8) & 0xff;
    bytes[offset + 2] = (value >> 16) & 0xff;
  }

  function writeUint32(bytes, offset, value) {
    bytes[offset] = value & 0xff;
    bytes[offset + 1] = (value >> 8) & 0xff;
    bytes[offset + 2] = (value >> 16) & 0xff;
    bytes[offset + 3] = (value >> 24) & 0xff;
  }

  function concatBytes(parts) {
    const total = parts.reduce((sum, part) => sum + part.length, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    for (const part of parts) {
      out.set(part, offset);
      offset += part.length;
    }
    return out;
  }

  function makeRiffChunk(type, payload) {
    const pad = payload.length % 2;
    const chunk = new Uint8Array(8 + payload.length + pad);
    chunk.set(asciiBytes(type), 0);
    writeUint32(chunk, 4, payload.length);
    chunk.set(payload, 8);
    return chunk;
  }

  function parseWebpChunks(bytes) {
    const text = (offset, length) => String.fromCharCode(...bytes.slice(offset, offset + length));
    if (text(0, 4) !== "RIFF" || text(8, 4) !== "WEBP") {
      throw new Error("Frame is not a WebP image.");
    }
    const chunks = [];
    let offset = 12;
    while (offset + 8 <= bytes.length) {
      const type = text(offset, 4);
      const size =
        bytes[offset + 4] |
        (bytes[offset + 5] << 8) |
        (bytes[offset + 6] << 16) |
        (bytes[offset + 7] << 24);
      const start = offset + 8;
      const end = start + size;
      if (end > bytes.length) break;
      chunks.push({ type, payload: bytes.slice(start, end) });
      offset = end + (size % 2);
    }
    return chunks;
  }

  function framePayloadFromWebp(dataUrl) {
    if (!dataUrl.startsWith("data:image/webp")) {
      throw new Error("This browser did not return WebP frame data.");
    }
    const chunks = parseWebpChunks(dataUrlToBytes(dataUrl));
    const imageChunks = chunks
      .filter((chunk) => chunk.type === "VP8 " || chunk.type === "VP8L" || chunk.type === "ALPH")
      .map((chunk) => makeRiffChunk(chunk.type, chunk.payload));
    if (!imageChunks.length) throw new Error("No WebP image payload was found.");
    return {
      payload: concatBytes(imageChunks),
      hasAlpha: chunks.some((chunk) => chunk.type === "ALPH" || chunk.type === "VP8L"),
    };
  }

  function makeAnimatedWebp(frames, width, height, frameDelayMs) {
    const hasAlpha = frames.some((frame) => frame.hasAlpha);
    const vp8x = new Uint8Array(10);
    vp8x[0] = 0x02 | (hasAlpha ? 0x10 : 0);
    writeUint24(vp8x, 4, width - 1);
    writeUint24(vp8x, 7, height - 1);

    const anim = new Uint8Array(6);
    const chunks = [makeRiffChunk("VP8X", vp8x), makeRiffChunk("ANIM", anim)];
    for (const frame of frames) {
      const header = new Uint8Array(16);
      writeUint24(header, 6, width - 1);
      writeUint24(header, 9, height - 1);
      writeUint24(header, 12, frameDelayMs);
      header[15] = 0x02;
      chunks.push(makeRiffChunk("ANMF", concatBytes([header, frame.payload])));
    }

    const riffPayload = concatBytes([asciiBytes("WEBP"), ...chunks]);
    const out = new Uint8Array(8 + riffPayload.length);
    out.set(asciiBytes("RIFF"), 0);
    writeUint32(out, 4, riffPayload.length);
    out.set(riffPayload, 8);
    return new Blob([out], { type: "image/webp" });
  }

  function waitForBrowserFrame() {
    return new Promise((resolve) => window.requestAnimationFrame(resolve));
  }

  function exportedSceneToBlob(exported, mimeType) {
    if (exported instanceof Blob) return exported;
    if (exported instanceof ArrayBuffer) return new Blob([exported], { type: mimeType });
    if (ArrayBuffer.isView(exported)) return new Blob([exported], { type: mimeType });
    return new Blob([exported], { type: mimeType });
  }

  function blankHandAdjustment() {
    return {
      position: { ...ZERO_VECTOR },
      rotation: { ...ZERO_VECTOR },
    };
  }

  function defaultHandAdjustments() {
    return Object.fromEntries(HAND_SLOTS.map((slot) => [slot, blankHandAdjustment()]));
  }

  function numericValue(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function parseVectorParam(value) {
    const parts = String(value || "").split(",").map((part) => numericValue(part.trim(), 0));
    return {
      x: parts[0] || 0,
      y: parts[1] || 0,
      z: parts[2] || 0,
    };
  }

  function vectorHasValue(vector) {
    return Boolean(vector && ["x", "y", "z"].some((axis) => Math.abs(numericValue(vector[axis], 0)) > 0.000001));
  }

  function vectorHashValue(vector) {
    return ["x", "y", "z"].map((axis) => numericValue(vector[axis], 0).toFixed(3).replace(/\.?0+$/, "") || "0").join(",");
  }

  function degreesToRadians(value) {
    return numericValue(value, 0) * Math.PI / 180;
  }

  async function loadJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`${url} returned ${response.status}`);
    return response.json();
  }

  function setMenu(toggleId, menuId) {
    const toggle = document.getElementById(toggleId);
    const menu = document.getElementById(menuId);
    if (!toggle || !menu) return;
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = menu.hidden;
      document.querySelectorAll(".rsdw-menu__panel").forEach((panel) => {
        panel.hidden = true;
      });
      document.querySelectorAll(".rsdw-iconbtn[aria-expanded]").forEach((btn) => {
        btn.setAttribute("aria-expanded", "false");
      });
      menu.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
    });
  }

  function slotRows(slot) {
    return (avatarIndex && avatarIndex.slots && avatarIndex.slots[slot]) || [];
  }

  function rowById(id) {
    if (!id) return null;
    for (const slot of SLOT_ORDER) {
      const found = slotRows(slot).find((row) => row.id === id);
      if (found) return found;
    }
    return null;
  }

  function baseAnimationRow() {
    return rowById(state && state.slots && state.slots.baseBody);
  }

  function animationsForAvatar(testState = state) {
    const body = rowById(testState && testState.slots && testState.slots.baseBody);
    if (!body || !animationIndex || !animationIndex.byModel) return [];
    const group = animationIndex.byModel[body.id];
    const rows = group && Array.isArray(group.animations) ? group.animations : [];
    return rows.filter((row) => row && row.status === "success" && row.gltfPath);
  }

  function animationById(id, testState = state) {
    if (!id) return null;
    return animationsForAvatar(testState).find((row) => row.id === id) || null;
  }

  function animationSearchText(row) {
    return [
      row.label,
      row.name,
      row.animationName,
      row.packagePath,
      row.gltfPath,
    ].filter(Boolean).join(" ").toLowerCase();
  }

  function filteredAnimationsForAvatar() {
    const rows = animationsForAvatar();
    const query = animationFilterText.trim().toLowerCase();
    if (!query) return rows;
    const terms = query.split(/\s+/).filter(Boolean);
    return rows.filter((row) => {
      const text = animationSearchText(row);
      return terms.every((term) => text.includes(term));
    });
  }

  function isTwoHandedRow(row) {
    return Boolean(row && row.isTwoHanded);
  }

  function normalizeHeldSlots(next) {
    const right = rowById(next.slots.rightHand);
    if (isTwoHandedRow(right)) {
      next.slots.leftHand = null;
    }
    const left = rowById(next.slots.leftHand);
    if (isTwoHandedRow(left)) {
      next.slots.rightHand = null;
    }
    return next;
  }

  function compatibleRows(slot) {
    const rows = slotRows(slot);
    if (slot === "hair") return rows;
    if (slot === "beard") {
      const head = rowById(state.slots.baseHead);
      return rows.filter((row) => row.sex === state.sex && (!head?.headFamily || !row.headFamily || row.headFamily === head.headFamily));
    }
    return rows.filter((row) => row.sex === state.sex || row.sex === "U_MED");
  }

  function firstCompatible(slot) {
    const rows = compatibleRows(slot);
    return rows.length ? rows[0].id : null;
  }

  function defaultState() {
    const defaults = avatarIndex.defaults || {};
    return {
      sex: GAME_START_DEFAULTS.sex,
      slots: { ...(defaults.slots || {}), ...GAME_START_DEFAULTS.slots },
      colors: { ...(defaults.colors || {}), ...GAME_START_DEFAULTS.colors },
      bodyVisible: defaults.bodyVisible !== false,
      headVisible: defaults.headVisible !== false,
      animation: null,
      handAdjustments: defaultHandAdjustments(),
    };
  }

  function parseHash() {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    if (!avatarIndex) return null;
    const next = defaultState();
    const sex = params.get("sex");
    if (sex === "M_MED" || sex === "F_MED") next.sex = sex;
    if (params.get("bodyVisible") === "0" || params.get("bodyVisible") === "false") {
      next.bodyVisible = false;
    }
    if (params.get("headVisible") === "0" || params.get("headVisible") === "false") {
      next.headVisible = false;
    }
    for (const slot of SLOT_ORDER) {
      if (params.has(slot)) {
        const value = params.get(slot);
        next.slots[slot] = value === "none" ? null : value;
      }
    }
    for (const role of COLOR_ROLES) {
      const colorKey = COLOR_HASH_KEYS[role];
      if (params.has(colorKey)) {
        next.colors[role] = params.get(colorKey);
      } else if (params.has(role)) {
        next.colors[role] = params.get(role);
      }
    }
    if (params.has("animation")) {
      const value = params.get("animation");
      next.animation = value === "none" ? null : value;
    }
    for (const slot of HAND_SLOTS) {
      const offsetKey = `${slot}Offset`;
      const rotationKey = `${slot}Rotation`;
      if (params.has(offsetKey)) {
        next.handAdjustments[slot].position = parseVectorParam(params.get(offsetKey));
      }
      if (params.has(rotationKey)) {
        next.handAdjustments[slot].rotation = parseVectorParam(params.get(rotationKey));
      }
    }
    return normalizeState(next);
  }

  function updateHash() {
    const params = new URLSearchParams();
    params.set("sex", state.sex);
    for (const slot of SLOT_ORDER) {
      const value = state.slots[slot];
      if (value) params.set(slot, value);
    }
    for (const role of COLOR_ROLES) {
      if (state.colors[role]) params.set(COLOR_HASH_KEYS[role], state.colors[role]);
    }
    if (!state.bodyVisible) params.set("bodyVisible", "0");
    if (!state.headVisible) params.set("headVisible", "0");
    if (state.animation) params.set("animation", state.animation);
    for (const slot of HAND_SLOTS) {
      if (!state.slots[slot]) continue;
      const adjustment = state.handAdjustments && state.handAdjustments[slot];
      if (vectorHasValue(adjustment && adjustment.position)) {
        params.set(`${slot}Offset`, vectorHashValue(adjustment.position));
      }
      if (vectorHasValue(adjustment && adjustment.rotation)) {
        params.set(`${slot}Rotation`, vectorHashValue(adjustment.rotation));
      }
    }
    window.history.replaceState(null, "", `#${params.toString()}`);
  }

  function normalizeState(next) {
    for (const slot of SLOT_ORDER) {
      const current = next.slots[slot];
      const rows = compatibleRowsForState(slot, next);
      if (current && rows.some((row) => row.id === current)) continue;
      next.slots[slot] = OPTIONAL_SLOTS.has(slot) ? null : (rows[0]?.id || null);
    }
    if (next.slots.helmet) {
      next.slots.hair = null;
    }
    normalizeHeldSlots(next);
    next.handAdjustments = { ...defaultHandAdjustments(), ...(next.handAdjustments || {}) };
    for (const slot of HAND_SLOTS) {
      const adjustment = next.handAdjustments[slot] || blankHandAdjustment();
      next.handAdjustments[slot] = {
        position: { ...ZERO_VECTOR, ...(adjustment.position || {}) },
        rotation: { ...ZERO_VECTOR, ...(adjustment.rotation || {}) },
      };
    }
    for (const role of COLOR_ROLES) {
      const colors = (avatarIndex.colors && avatarIndex.colors[role]) || [];
      if (!colors.some((color) => color.id === next.colors[role])) {
        next.colors[role] = colors[0]?.id || "";
      }
    }
    if (next.animation && !animationById(next.animation, next)) {
      next.animation = null;
    }
    return next;
  }

  function compatibleRowsForState(slot, testState) {
    const rows = slotRows(slot);
    if (slot === "hair") return rows;
    if (slot === "beard") {
      const head = rowById(testState.slots.baseHead);
      return rows.filter((row) => row.sex === testState.sex && (!head?.headFamily || !row.headFamily || row.headFamily === head.headFamily));
    }
    return rows.filter((row) => row.sex === testState.sex || row.sex === "U_MED");
  }

  function fillSlotSelect(slot) {
    const select = els.slotControls[slot];
    const rows = compatibleRows(slot);
    const lockedByTwoHander =
      (slot === "leftHand" && isTwoHandedRow(rowById(state.slots.rightHand))) ||
      (slot === "rightHand" && isTwoHandedRow(rowById(state.slots.leftHand)));
    select.textContent = "";
    if (OPTIONAL_SLOTS.has(slot)) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = lockedByTwoHander ? "Two-handed item selected" : "None";
      select.appendChild(option);
    }
    for (const row of lockedByTwoHander ? [] : rows) {
      const option = document.createElement("option");
      option.value = row.id;
      option.textContent = row.label;
      if (row.isTwoHanded) option.textContent = `${row.label} (2H)`;
      select.appendChild(option);
    }
    select.value = state.slots[slot] || "";
    select.disabled = lockedByTwoHander;
  }

  function fillHandAdjustmentControls() {
    for (const slot of HAND_SLOTS) {
      const panel = els.handAdjustPanels[slot];
      const disabled = !state.slots[slot];
      if (panel) panel.classList.toggle("is-disabled", disabled);
    }
    for (const input of els.handAdjustInputs) {
      const slot = input.dataset.hand;
      const kind = input.dataset.kind;
      const axis = input.dataset.axis;
      const adjustment = state.handAdjustments?.[slot] || blankHandAdjustment();
      const value = numericValue(adjustment[kind]?.[axis], 0);
      input.value = Number.isInteger(value) ? String(value) : String(Number(value.toFixed(3)));
      input.disabled = !state.slots[slot];
    }
    for (const button of els.handAdjustReset) {
      button.disabled = !state.slots[button.dataset.hand];
    }
  }

  function fillAnimationSelect() {
    const select = els.animationSelect;
    const allRows = animationsForAvatar();
    let rows = filteredAnimationsForAvatar();
    const selectedRow = animationById(state.animation);
    if (selectedRow && !rows.some((row) => row.id === selectedRow.id)) {
      rows = [selectedRow, ...rows];
    }
    select.textContent = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = animationFilterText.trim() ? `None (${rows.length}/${allRows.length})` : "None";
    select.appendChild(none);
    for (const row of rows) {
      const option = document.createElement("option");
      option.value = row.id;
      option.textContent = row.label || row.name || row.id;
      select.appendChild(option);
    }
    select.value = state.animation || "";
    select.disabled = allRows.length === 0;
    els.animationPlay.disabled = !state.animation;
    els.animationCapture.disabled = !state.animation || activeMixers.length === 0 || !activeAnimationDuration;
    els.animationPlay.textContent = animationPlaying ? "Pause" : "Play";
  }

  function renderControls() {
    els.sexM.classList.toggle("is-active", state.sex === "M_MED");
    els.sexF.classList.toggle("is-active", state.sex === "F_MED");
    els.bodyVisible.checked = state.bodyVisible;
    els.headVisible.checked = state.headVisible;
    for (const slot of SLOT_ORDER) fillSlotSelect(slot);
    fillHandAdjustmentControls();
    fillAnimationSelect();
    for (const role of COLOR_ROLES) {
      const container = els.swatches[role];
      container.querySelectorAll(".swatch").forEach((button) => {
        button.classList.toggle("is-active", button.dataset.color === state.colors[role]);
      });
    }
  }

  function renderSwatches() {
    for (const role of COLOR_ROLES) {
      const container = els.swatches[role];
      container.textContent = "";
      for (const color of avatarIndex.colors[role] || []) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "swatch";
        button.dataset.role = role;
        button.dataset.color = color.id;
        button.style.setProperty("--swatch", color.hex);
        button.title = color.label;
        button.setAttribute("aria-label", color.label);
        button.addEventListener("click", () => {
          state.colors[role] = color.id;
          renderControls();
          updateAvatar();
        });
        container.appendChild(button);
      }
    }
  }

  function initThree() {
    scene = new THREE.Scene();
    clock = new THREE.Clock();
    scene.background = null;
    camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
    camera.position.set(0, 1.2, 4.2);
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(els.stage.clientWidth, els.stage.clientHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.0;
    els.stage.appendChild(renderer.domElement);

    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    pmrem.dispose();

    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.target.set(0, 1.0, 0);
    controls.minDistance = 1.0;
    controls.maxDistance = 8.0;

    avatarRoot = new THREE.Group();
    scene.add(avatarRoot);

    scene.add(new THREE.HemisphereLight(0xfff1dd, 0x1e2532, 2.3));
    const key = new THREE.DirectionalLight(0xffe2ba, 2.2);
    key.position.set(2.5, 3.2, 3.5);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xa8c7ff, 0.9);
    fill.position.set(-3, 2, -1);
    scene.add(fill);

    const draco = new DRACOLoader();
    draco.setDecoderPath("https://unpkg.com/three@0.184.0/examples/jsm/libs/draco/");
    loader = new GLTFLoader();
    loader.setDRACOLoader(draco);

    window.addEventListener("resize", resize);
    resize();
    animate();
  }

  function resize() {
    if (!renderer || !camera) return;
    const width = Math.max(1, els.stage.clientWidth);
    const height = Math.max(1, els.stage.clientHeight);
    renderer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }

  function animate() {
    requestAnimationFrame(animate);
    const delta = clock ? clock.getDelta() : 0;
    if (!isCapturingAnimation && !isExportingAvatar) {
      for (const mixer of activeMixers) {
        mixer.update(delta);
      }
    }
    controls.update();
    renderer.render(scene, camera);
  }

  async function loadGltf(gltfPath) {
    if (!gltfCache.has(gltfPath)) {
      gltfCache.set(gltfPath, loader.loadAsync(assetUrl(gltfPath)));
    }
    return gltfCache.get(gltfPath);
  }

  async function loadAnimationClip(row) {
    if (!row || !row.gltfPath) return null;
    const gltf = await loadGltf(row.gltfPath);
    if (!gltf.animations || !gltf.animations.length) return null;
    const preferred = row.animationName || row.name;
    return gltf.animations.find((clip) => clip.name === preferred) || gltf.animations[0];
  }

  async function loadTexture(relPath) {
    if (!textureCache.has(relPath)) {
      textureCache.set(relPath, new Promise((resolve, reject) => {
        textureLoader.load(assetUrl(relPath), (texture) => {
          texture.colorSpace = THREE.SRGBColorSpace;
          texture.flipY = false;
          resolve(texture);
        }, undefined, reject);
      }));
    }
    return textureCache.get(relPath);
  }

  function cloneScene(source) {
    const cloned = SkeletonUtils.clone(source);
    cloned.traverse((obj) => {
      if (!obj.isMesh && !obj.isSkinnedMesh) return;
      obj.frustumCulled = false;
      obj.castShadow = false;
      obj.receiveShadow = false;
      if (Array.isArray(obj.material)) {
        obj.material = obj.material.map((mat) => mat.clone());
      } else if (obj.material) {
        obj.material = obj.material.clone();
      }
    });
    return cloned;
  }

  async function applyVariants(root, row) {
    const variants = row.materialVariants || {};
    const jobs = [];
    root.traverse((obj) => {
      if (!obj.isMesh && !obj.isSkinnedMesh) return;
      const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
      for (const mat of materials) {
        if (!mat || !mat.name) continue;
        for (const role of COLOR_ROLES) {
          const roleVariants = variants[role] || {};
          const colorId = state.colors[role];
          const relPath = roleVariants[mat.name] && roleVariants[mat.name][colorId];
          if (!relPath) continue;
          jobs.push(loadTexture(relPath).then((texture) => {
            mat.map = texture;
            mat.color.set(0xffffff);
            mat.needsUpdate = true;
          }));
        }
      }
    });
    await Promise.all(jobs);
  }

  async function loadSlot(slot, row) {
    const previous = activeObjects.get(slot);
    if (previous) {
      if (previous.parent) previous.parent.remove(previous);
      activeObjects.delete(slot);
    }
    if (!row) return;
    const gltf = await loadGltf(row.gltfPath);
    const cloned = cloneScene(gltf.scene);
    await applyVariants(cloned, row);
    avatarRoot.add(cloned);
    activeObjects.set(slot, cloned);
  }

  function findNamedDescendant(root, names) {
    for (const name of names) {
      const exact = root.getObjectByName(name);
      if (exact) return exact;
      const needle = String(name).toLowerCase();
      let found = null;
      root.traverse((obj) => {
        if (!found && String(obj.name || "").toLowerCase() === needle) {
          found = obj;
        }
      });
      if (found) return found;
    }
    return null;
  }

  function findAvatarAttachment(row, slot) {
    const names = row.attachFallbacks || DEFAULT_ATTACH_FALLBACKS[slot] || [];
    for (const sourceSlot of ATTACHMENT_SOURCE_SLOTS) {
      const root = activeObjects.get(sourceSlot);
      if (!root) continue;
      const target = findNamedDescendant(root, names);
      if (target) return target;
    }
    return null;
  }

  function resetHeldTransform(root, row, slot) {
    const category = String(row.category || "");
    const isBow = /\bBow\b/i.test(category) && !/Crossbow/i.test(category);
    const baseOffset = row.category === "Shield" ? SHIELD_HELD_OFFSET : isBow ? BOW_HELD_OFFSET : {};
    const offset = { ...baseOffset, ...(row.attachOffset || {}) };
    const baseRotation = row.category === "Shield" ? SHIELD_HELD_ROTATION : isBow ? BOW_HELD_ROTATION : DEFAULT_HELD_ROTATION;
    const rotation = { ...baseRotation, ...(row.attachRotation || {}) };
    const adjustment = state.handAdjustments?.[slot] || blankHandAdjustment();
    root.position.set(
      numericValue(offset.x, 0) + numericValue(adjustment.position.x, 0),
      numericValue(offset.y, 0) + numericValue(adjustment.position.y, 0),
      numericValue(offset.z, 0) + numericValue(adjustment.position.z, 0),
    );
    root.rotation.set(
      numericValue(rotation.x, 0) + degreesToRadians(adjustment.rotation.x),
      numericValue(rotation.y, 0) + degreesToRadians(adjustment.rotation.y),
      numericValue(rotation.z, 0) + degreesToRadians(adjustment.rotation.z),
    );
    root.scale.setScalar(Number(row.attachScale || 1));
    root.updateMatrixWorld(true);
  }

  function attachHeldSlots() {
    const missing = [];
    for (const slot of HELD_SLOTS) {
      const row = rowById(state.slots[slot]);
      const root = activeObjects.get(slot);
      if (!row || !root) continue;
      const target = findAvatarAttachment(row, slot);
      if (!target) {
        missing.push(row.label || row.displayName || slot);
        continue;
      }
      target.add(root);
      resetHeldTransform(root, row, slot);
    }
    if (missing.length) {
      setWarning(`Could not find hand attachment bones for: ${missing.join(", ")}.`);
    }
  }

  function clearAnimationMixers() {
    while (activeMixers.length) {
      const mixer = activeMixers.pop();
      mixer.stopAllAction();
      mixer.uncacheRoot(mixer.getRoot());
    }
  }

  async function applySelectedAnimation() {
    clearAnimationMixers();
    activeAnimationDuration = 0;
    els.animationCapture.disabled = true;
    if (!state.animation) {
      els.animationPlay.disabled = true;
      els.animationPlay.textContent = "Play";
      return;
    }
    const row = animationById(state.animation);
    if (!row) {
      els.animationPlay.disabled = true;
      return;
    }
    const clip = await loadAnimationClip(row);
    if (!clip) {
      setWarning("The selected animation did not contain playable clip data.");
      els.animationPlay.disabled = true;
      return;
    }
    activeAnimationDuration = Number(clip.duration || row.duration_s || 0);
    for (const [slot, root] of activeObjects.entries()) {
      if (HELD_SLOTS.has(slot)) continue;
      const mixer = new THREE.AnimationMixer(root);
      const action = mixer.clipAction(clip);
      action.reset().play();
      mixer.timeScale = animationPlaying ? 1 : 0;
      activeMixers.push(mixer);
    }
    els.animationPlay.disabled = activeMixers.length === 0;
    els.animationCapture.disabled = activeMixers.length === 0 || !activeAnimationDuration;
    els.animationPlay.textContent = animationPlaying ? "Pause" : "Play";
  }

  async function captureAvatarAnimationWebp() {
    if (!state.animation || !activeMixers.length || !activeAnimationDuration || els.animationCapture.disabled) return;
    const previousText = els.animationCapture.textContent;
    const previousTimes = activeMixers.map((mixer) => mixer.time);
    const previousScales = activeMixers.map((mixer) => mixer.timeScale);
    const duration = activeAnimationDuration;
    const frameRate = Math.max(
      4,
      Math.min(ANIMATION_CAPTURE_TARGET_FPS, Math.floor(ANIMATION_CAPTURE_MAX_FRAMES / duration) || ANIMATION_CAPTURE_TARGET_FPS),
    );
    const frameCount = Math.max(2, Math.ceil(duration * frameRate));
    const frameDelayMs = Math.max(20, Math.round((duration * 1000) / frameCount));

    els.animationCapture.disabled = true;
    els.animationPlay.disabled = true;
    els.animationCapture.textContent = "Capturing 0%";
    try {
      isCapturingAnimation = true;
      for (const mixer of activeMixers) mixer.timeScale = 1;
      const frames = [];
      for (let frameIndex = 0; frameIndex < frameCount; frameIndex += 1) {
        const time = (duration * frameIndex) / frameCount;
        for (const mixer of activeMixers) mixer.setTime(time);
        controls.update();
        renderer.render(scene, camera);
        const dataUrl = renderer.domElement.toDataURL("image/webp", ANIMATION_CAPTURE_QUALITY);
        frames.push(framePayloadFromWebp(dataUrl));
        els.animationCapture.textContent = `Capturing ${Math.round(((frameIndex + 1) / frameCount) * 100)}%`;
        if (frameIndex % 4 === 0) await waitForBrowserFrame();
      }
      const blob = makeAnimatedWebp(frames, renderer.domElement.width, renderer.domElement.height, frameDelayMs);
      downloadBlob(blob, avatarAnimationFileName());
      els.animationCapture.textContent = "Captured";
      window.setTimeout(() => {
        if (state.animation) els.animationCapture.textContent = previousText;
      }, 1200);
    } catch (error) {
      console.error(error);
      setWarning("Animation capture failed. Try again after the avatar finishes loading.");
      els.animationCapture.textContent = previousText;
    } finally {
      isCapturingAnimation = false;
      activeMixers.forEach((mixer, index) => {
        mixer.timeScale = 1;
        mixer.setTime(previousTimes[index] || 0);
        mixer.timeScale = previousScales[index] ?? (animationPlaying ? 1 : 0);
      });
      controls.update();
      renderer.render(scene, camera);
      els.animationPlay.disabled = !state.animation || activeMixers.length === 0;
      els.animationCapture.disabled = !state.animation || activeMixers.length === 0 || !activeAnimationDuration;
      els.animationPlay.textContent = animationPlaying ? "Pause" : "Play";
    }
  }

  function selectedRows() {
    return SLOT_ORDER
      .filter((slot) => slot !== "baseBody" || state.bodyVisible)
      .filter((slot) => slot !== "baseHead" || state.headVisible)
      .map((slot) => [slot, rowById(state.slots[slot])])
      .filter(([, row]) => row);
  }

  function avatarHasVisibleGeometry() {
    return Boolean(avatarRoot && avatarRoot.children.length);
  }

  function setAvatarDownloadButtons(disabled, message) {
    const shouldDisable = disabled || !avatarHasVisibleGeometry() || isExportingAvatar;
    els.download.disabled = shouldDisable;
    els.downloadGlb.disabled = shouldDisable;
    els.downloadStl.disabled = shouldDisable;
    if (message) els.downloadStatus.textContent = message;
  }

  function setAvatarDownloadDialogOpen(open) {
    els.downloadDialog.hidden = !open;
    document.body.classList.toggle("modal-open", open);
    if (open) {
      els.downloadStatus.textContent = state.animation
        ? "Exports use the current visible animation pose where possible."
        : "Exports run locally in your browser.";
      window.setTimeout(() => els.downloadGlb.focus(), 0);
    } else {
      window.setTimeout(() => els.download.focus(), 0);
    }
  }

  function openAvatarDownloadDialog() {
    if (els.download.disabled) return;
    setAvatarDownloadDialogOpen(true);
  }

  function closeAvatarDownloadDialog() {
    if (isExportingAvatar) return;
    setAvatarDownloadDialogOpen(false);
  }

  function syncAvatarPoseForExport() {
    if (!avatarRoot || !renderer || !scene || !camera) return;
    for (const mixer of activeMixers) mixer.update(0);
    avatarRoot.updateMatrixWorld(true);
    renderer.render(scene, camera);
  }

  async function exportAvatarGlbBlob() {
    syncAvatarPoseForExport();
    const exporter = new GLTFExporter();
    return new Promise((resolve, reject) => {
      exporter.parse(
        avatarRoot,
        (exported) => resolve(exportedSceneToBlob(exported, "model/gltf-binary")),
        reject,
        {
          binary: true,
          trs: true,
          onlyVisible: true,
          maxTextureSize: Infinity,
          includeCustomExtensions: false,
        },
      );
    });
  }

  function isVisibleInHierarchy(object) {
    let current = object;
    while (current && current !== avatarRoot.parent) {
      if (!current.visible) return false;
      current = current.parent;
    }
    return true;
  }

  function buildPoseBakedStlScene() {
    syncAvatarPoseForExport();
    const bakedRoot = new THREE.Group();
    const position = new THREE.Vector3();
    avatarRoot.traverse((object) => {
      if ((!object.isMesh && !object.isSkinnedMesh) || !isVisibleInHierarchy(object)) return;
      const source = object.geometry;
      const sourcePositions = source && source.attributes && source.attributes.position;
      if (!sourcePositions || sourcePositions.count <= 0) return;
      if (object.isSkinnedMesh && object.skeleton) object.skeleton.update();
      const positions = new Float32Array(sourcePositions.count * 3);
      for (let index = 0; index < sourcePositions.count; index += 1) {
        if (object.isSkinnedMesh && typeof object.getVertexPosition === "function") {
          object.getVertexPosition(index, position);
        } else {
          position.fromBufferAttribute(sourcePositions, index);
        }
        object.localToWorld(position);
        positions[index * 3] = position.x;
        positions[index * 3 + 1] = position.y;
        positions[index * 3 + 2] = position.z;
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      if (source.index) geometry.setIndex(source.index.clone());
      geometry.computeVertexNormals();
      bakedRoot.add(new THREE.Mesh(geometry, new THREE.MeshBasicMaterial()));
    });
    return bakedRoot;
  }

  async function downloadAvatarGlb() {
    if (isExportingAvatar || !avatarHasVisibleGeometry()) return;
    isExportingAvatar = true;
    setAvatarDownloadButtons(true, state.animation ? "Preparing GLB with the current visible pose..." : "Preparing GLB...");
    let resultMessage = null;
    let resetMessage = null;
    try {
      await waitForBrowserFrame();
      const blob = await exportAvatarGlbBlob();
      downloadBlob(blob, `${avatarExportBaseName()}.glb`);
      resultMessage = "GLB download started.";
      resetMessage = "Exports run locally in your browser.";
    } catch (error) {
      console.error(error);
      resultMessage = "GLB export failed. Try again after the avatar finishes loading.";
    } finally {
      isExportingAvatar = false;
      setAvatarDownloadButtons(false, resultMessage);
      if (resetMessage) window.setTimeout(() => setAvatarDownloadButtons(false, resetMessage), 1200);
    }
  }

  async function downloadAvatarStl() {
    if (isExportingAvatar || !avatarHasVisibleGeometry()) return;
    isExportingAvatar = true;
    setAvatarDownloadButtons(true, state.animation ? "Baking current animation pose to STL..." : "Preparing geometry for STL...");
    let resultMessage = null;
    let resetMessage = null;
    try {
      await waitForBrowserFrame();
      const bakedRoot = buildPoseBakedStlScene();
      if (!bakedRoot.children.length) throw new Error("No visible geometry was found.");
      const stl = new STLExporter().parse(bakedRoot, { binary: true });
      downloadBlob(exportedSceneToBlob(stl, "model/stl"), `${avatarExportBaseName()}.stl`);
      resultMessage = "STL download started.";
      resetMessage = "STL is geometry-only; textures and materials are not included.";
    } catch (error) {
      console.error(error);
      resultMessage = "STL export failed. This avatar may be too large or unsupported in this browser.";
    } finally {
      isExportingAvatar = false;
      setAvatarDownloadButtons(false, resultMessage);
      if (resetMessage) window.setTimeout(() => setAvatarDownloadButtons(false, resetMessage), 1200);
    }
  }

  function fitCamera() {
    if (!avatarRoot.children.length) return;
    const box = new THREE.Box3().setFromObject(avatarRoot);
    if (box.isEmpty()) return;
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const maxSize = Math.max(size.x, size.y, size.z, 0.5);
    const distance = maxSize / (2 * Math.tan((camera.fov * Math.PI) / 360));
    controls.target.copy(center);
    camera.position.set(center.x, center.y + size.y * 0.08, center.z + distance * 1.75);
    camera.near = Math.max(0.01, distance / 100);
    camera.far = Math.max(100, distance * 10);
    camera.updateProjectionMatrix();
    controls.update();
  }

  function setWarning(message) {
    els.warning.hidden = !message;
    els.warning.textContent = message || "";
  }

  function updateSummary() {
    const parts = selectedRows()
      .map(([slot, row]) => `${SLOT_LABELS[slot] || slot.replace("base", "")}: ${row.label}`)
      .slice(0, 5);
    if (!state.bodyVisible) parts.unshift("Body: hidden");
    if (!state.headVisible) parts.unshift("Head: hidden");
    const animation = animationById(state.animation);
    if (animation) parts.push(`Animation: ${animation.label || animation.name}`);
    els.summary.textContent = parts.join(" | ") || "Default loadout";
  }

  async function updateAvatar() {
    els.loading.hidden = false;
    els.animationCapture.disabled = true;
    setAvatarDownloadButtons(true, "Avatar is loading...");
    setWarning("");
    renderControls();
    updateHash();
    updateSummary();
    try {
      const rows = selectedRows();
      const visibleSlots = new Set(rows.map(([slot]) => slot));
      await Promise.all(rows.map(([slot, row]) => loadSlot(slot, row)));
      for (const slot of SLOT_ORDER) {
        if (!visibleSlots.has(slot)) await loadSlot(slot, null);
      }
      attachHeldSlots();
      await applySelectedAnimation();
      if (!hasFitCamera) {
        fitCamera();
        hasFitCamera = true;
      }
      els.status.textContent = `${rows.length} layered model${rows.length === 1 ? "" : "s"}.`;
      els.resetView.disabled = false;
      els.screenshot.disabled = false;
      setAvatarDownloadButtons(false, "Exports run locally in your browser.");
      els.copyLink.disabled = false;
      els.openModel.disabled = false;
    } catch (error) {
      console.error(error);
      setWarning("The avatar could not finish loading. Confirm the selected WebAssets exist on this branch.");
      setAvatarDownloadButtons(true, "Download unavailable because the avatar failed to load.");
    } finally {
      els.loading.hidden = true;
    }
  }

  function setSex(sex) {
    state.sex = sex;
    state = normalizeState(state);
    renderControls();
    updateAvatar();
  }

  function bindEvents() {
    setMenu("discord-toggle", "discord-menu");
    setMenu("links-toggle", "links-menu");
    document.addEventListener("click", () => {
      document.querySelectorAll(".rsdw-menu__panel").forEach((panel) => {
        panel.hidden = true;
      });
      document.querySelectorAll(".rsdw-iconbtn[aria-expanded]").forEach((btn) => {
        btn.setAttribute("aria-expanded", "false");
      });
    });

    els.sexM.addEventListener("click", () => setSex("M_MED"));
    els.sexF.addEventListener("click", () => setSex("F_MED"));
    for (const slot of SLOT_ORDER) {
      const select = els.slotControls[slot];
      select.addEventListener("change", () => {
        activeSlot = slot;
        state.slots[slot] = select.value || null;
        if (slot === "leftHand" && state.slots.leftHand && isTwoHandedRow(rowById(state.slots.rightHand))) {
          state.slots.rightHand = null;
        }
        if (slot === "rightHand" && isTwoHandedRow(rowById(state.slots.rightHand))) {
          state.slots.leftHand = null;
        }
        if (slot === "leftHand" && isTwoHandedRow(rowById(state.slots.leftHand))) {
          state.slots.rightHand = null;
        }
        if (slot === "rightHand" && state.slots.rightHand && isTwoHandedRow(rowById(state.slots.leftHand))) {
          state.slots.leftHand = null;
        }
        state = normalizeState(state);
        renderControls();
        updateAvatar();
      });
      select.addEventListener("focus", () => {
        activeSlot = slot;
      });
    }

    els.bodyVisible.addEventListener("change", () => {
      state.bodyVisible = els.bodyVisible.checked;
      updateAvatar();
    });
    els.headVisible.addEventListener("change", () => {
      state.headVisible = els.headVisible.checked;
      updateAvatar();
    });
    for (const input of els.handAdjustInputs) {
      input.addEventListener("input", () => {
        const slot = input.dataset.hand;
        const kind = input.dataset.kind;
        const axis = input.dataset.axis;
        if (!state.handAdjustments[slot]) state.handAdjustments[slot] = blankHandAdjustment();
        state.handAdjustments[slot][kind][axis] = numericValue(input.value, 0);
        attachHeldSlots();
        updateHash();
      });
    }
    for (const button of els.handAdjustReset) {
      button.addEventListener("click", () => {
        const slot = button.dataset.hand;
        state.handAdjustments[slot] = blankHandAdjustment();
        fillHandAdjustmentControls();
        attachHeldSlots();
        updateHash();
      });
    }
    els.animationSelect.addEventListener("change", () => {
      state.animation = els.animationSelect.value || null;
      animationPlaying = Boolean(state.animation);
      updateAvatar();
    });
    els.animationFilter.addEventListener("input", () => {
      animationFilterText = els.animationFilter.value || "";
      fillAnimationSelect();
    });
    els.animationPlay.addEventListener("click", () => {
      if (!state.animation) return;
      animationPlaying = !animationPlaying;
      for (const mixer of activeMixers) {
        mixer.timeScale = animationPlaying ? 1 : 0;
      }
      els.animationPlay.textContent = animationPlaying ? "Pause" : "Play";
    });
    els.animationCapture.addEventListener("click", captureAvatarAnimationWebp);
    els.download.addEventListener("click", openAvatarDownloadDialog);
    els.downloadClose.addEventListener("click", closeAvatarDownloadDialog);
    els.downloadBackdrop.addEventListener("click", closeAvatarDownloadDialog);
    els.downloadGlb.addEventListener("click", downloadAvatarGlb);
    els.downloadStl.addEventListener("click", downloadAvatarStl);
    els.resetView.addEventListener("click", () => {
      fitCamera();
      hasFitCamera = true;
    });
    els.screenshot.addEventListener("click", () => {
      renderer.domElement.toBlob((blob) => {
        if (!blob) return;
        downloadBlob(blob, `RSDWModel_Avatar_${state.sex}.png`);
      }, "image/png");
    });
    els.copyLink.addEventListener("click", async () => {
      const url = `${window.location.origin}${window.location.pathname}${window.location.hash}`;
      try {
        await navigator.clipboard.writeText(url);
        els.copyLink.textContent = "Copied";
        window.setTimeout(() => {
          els.copyLink.textContent = "Copy Link";
        }, 1200);
      } catch {
        window.prompt("Copy avatar link", url);
      }
    });
    els.openModel.addEventListener("click", () => {
      const row = rowById(state.slots[activeSlot]) || rowById(state.slots.baseHead) || rowById(state.slots.baseBody);
      if (row) window.open(githubModelUrl(row), "_blank", "noopener,noreferrer");
    });
  }

  async function init() {
    bindEvents();
    config = { ...DEFAULT_CONFIG, ...(await loadJson(CONFIG_URL).catch(() => ({}))) };
    avatarIndex = await loadJson(AVATAR_INDEX_URL);
    animationIndex = await loadJson(ANIMATION_INDEX_URL).catch(() => null);
    config.datasetVersion = avatarIndex.datasetVersion || config.datasetVersion;
    state = parseHash() || normalizeState(defaultState());
    initThree();
    renderSwatches();
    renderControls();
    await updateAvatar();
  }

  init().catch((error) => {
    console.error(error);
    els.status.textContent = "Unable to load avatar data.";
    setWarning(error.message);
  });
})();
