import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import * as SkeletonUtils from "three/addons/utils/SkeletonUtils.js";

(function () {
  "use strict";

  const CONFIG_URL = "../data.config.json";
  const AVATAR_INDEX_URL = "../avatar-index.json";
  const DEFAULT_CONFIG = {
    repoOwner: "RSDWArchive",
    repoName: "RSDWModel",
    repoBranch: "main",
    datasetVersion: "0.11.2.2",
    assetBaseUrl: "auto",
  };
  const SLOT_ORDER = ["baseBody", "baseHead", "hair", "beard", "torso", "legs", "helmet", "cape"];
  const OPTIONAL_SLOTS = new Set(["hair", "beard", "torso", "legs", "helmet", "cape"]);
  const COLOR_ROLES = ["skin", "hair", "eyes"];
  const COLOR_HASH_KEYS = {
    skin: "skinColor",
    hair: "hairColor",
    eyes: "eyeColor",
  };

  const els = {
    stage: document.getElementById("avatar-stage"),
    loading: document.getElementById("avatar-loading"),
    status: document.getElementById("avatar-status"),
    warning: document.getElementById("avatar-warning"),
    summary: document.getElementById("avatar-summary"),
    resetView: document.getElementById("reset-view"),
    screenshot: document.getElementById("avatar-screenshot"),
    copyLink: document.getElementById("copy-avatar-link"),
    openModel: document.getElementById("open-current-model"),
    sexM: document.getElementById("sex-m"),
    sexF: document.getElementById("sex-f"),
    slotControls: Object.fromEntries(SLOT_ORDER.map((slot) => [slot, document.getElementById(`slot-${slot}`)])),
    swatches: {
      skin: document.getElementById("skin-swatches"),
      hair: document.getElementById("hair-swatches"),
      eyes: document.getElementById("eye-swatches"),
    },
  };

  let config = { ...DEFAULT_CONFIG };
  let avatarIndex = null;
  let state = null;
  let activeSlot = "baseHead";
  let renderer = null;
  let scene = null;
  let camera = null;
  let controls = null;
  let avatarRoot = null;
  let loader = null;
  const gltfCache = new Map();
  const textureCache = new Map();
  const activeObjects = new Map();
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
      sex: defaults.sex || "M_MED",
      slots: { ...(defaults.slots || {}) },
      colors: { ...(defaults.colors || {}) },
    };
  }

  function parseHash() {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    if (!avatarIndex) return null;
    const next = defaultState();
    const sex = params.get("sex");
    if (sex === "M_MED" || sex === "F_MED") next.sex = sex;
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
    for (const role of COLOR_ROLES) {
      const colors = (avatarIndex.colors && avatarIndex.colors[role]) || [];
      if (!colors.some((color) => color.id === next.colors[role])) {
        next.colors[role] = colors[0]?.id || "";
      }
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
    select.textContent = "";
    if (OPTIONAL_SLOTS.has(slot)) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "None";
      select.appendChild(option);
    }
    for (const row of rows) {
      const option = document.createElement("option");
      option.value = row.id;
      option.textContent = row.label;
      select.appendChild(option);
    }
    select.value = state.slots[slot] || "";
  }

  function renderControls() {
    els.sexM.classList.toggle("is-active", state.sex === "M_MED");
    els.sexF.classList.toggle("is-active", state.sex === "F_MED");
    for (const slot of SLOT_ORDER) fillSlotSelect(slot);
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
    controls.update();
    renderer.render(scene, camera);
  }

  async function loadGltf(gltfPath) {
    if (!gltfCache.has(gltfPath)) {
      gltfCache.set(gltfPath, loader.loadAsync(assetUrl(gltfPath)));
    }
    return gltfCache.get(gltfPath);
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
      avatarRoot.remove(previous);
      activeObjects.delete(slot);
    }
    if (!row) return;
    const gltf = await loadGltf(row.gltfPath);
    const cloned = cloneScene(gltf.scene);
    await applyVariants(cloned, row);
    avatarRoot.add(cloned);
    activeObjects.set(slot, cloned);
  }

  function selectedRows() {
    return SLOT_ORDER.map((slot) => [slot, rowById(state.slots[slot])]).filter(([, row]) => row);
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
      .map(([slot, row]) => `${slot.replace("base", "")}: ${row.label}`)
      .slice(0, 5);
    els.summary.textContent = parts.join(" | ") || "Default loadout";
  }

  async function updateAvatar() {
    els.loading.hidden = false;
    setWarning("");
    renderControls();
    updateHash();
    updateSummary();
    try {
      const rows = selectedRows();
      await Promise.all(rows.map(([slot, row]) => loadSlot(slot, row)));
      for (const slot of SLOT_ORDER) {
        if (!state.slots[slot]) await loadSlot(slot, null);
      }
      fitCamera();
      els.status.textContent = `${rows.length} layered model${rows.length === 1 ? "" : "s"}.`;
      els.resetView.disabled = false;
      els.screenshot.disabled = false;
      els.copyLink.disabled = false;
      els.openModel.disabled = false;
    } catch (error) {
      console.error(error);
      setWarning("The avatar could not finish loading. Confirm the selected WebAssets exist on this branch.");
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
        state = normalizeState(state);
        renderControls();
        updateAvatar();
      });
      select.addEventListener("focus", () => {
        activeSlot = slot;
      });
    }

    els.resetView.addEventListener("click", fitCamera);
    els.screenshot.addEventListener("click", () => {
      renderer.domElement.toBlob((blob) => {
        if (!blob) return;
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `RSDWModel_Avatar_${state.sex}.png`;
        document.body.appendChild(link);
        link.click();
        URL.revokeObjectURL(link.href);
        link.remove();
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
    config.datasetVersion = avatarIndex.datasetVersion || config.datasetVersion;
    state = parseHash() || defaultState();
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
