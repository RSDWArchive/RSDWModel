(function () {
  "use strict";

  const CONFIG_URL = "./data.config.json";
  const INDEX_URL = "./model-index.json";
  const VARIANT_INDEX_URL = "./equipment-variants.json";
  const ANIMATION_INDEX_URL = "./animation-index.json";
  const RESULTS_PAGE_SIZE = 120;
  const SEARCH_DEBOUNCE_MS = 80;
  const ANIMATION_CAPTURE_TARGET_FPS = 30;
  const ANIMATION_CAPTURE_MAX_FRAMES = 450;
  const ANIMATION_CAPTURE_QUALITY = 0.82;
  const VIEWER_PREFS_STORAGE_KEY = "rsdwmodel.viewerPrefs.v1";
  const VIEWER_CONTROLS_STORAGE_KEY = "rsdwmodel.viewerInspectorOpen.v2";
  const VIEWER_TAB_STORAGE_KEY = "rsdwmodel.viewerInspectorTab.v1";
  const CUSTOM_CAMERA_STORAGE_KEY = "rsdwmodel.customCameraView.v1";
  const DEFAULT_FIELD_OF_VIEW = 30;
  const LIGHTING_PRESETS = {
    neutral: { exposure: 0.92, shadowIntensity: 0.35, shadowSoftness: 1, toneMapping: "neutral", environment: "neutral" },
    studio: { exposure: 1.04, shadowIntensity: 0.55, shadowSoftness: 0.8, toneMapping: "neutral", environment: "neutral" },
    soft: { exposure: 1.08, shadowIntensity: 0.18, shadowSoftness: 1, toneMapping: "neutral", environment: "neutral" },
    contrast: { exposure: 0.88, shadowIntensity: 0.9, shadowSoftness: 0.42, toneMapping: "aces", environment: "neutral" },
    warm: { exposure: 1.02, shadowIntensity: 0.4, shadowSoftness: 0.85, toneMapping: "cineon", environment: "legacy" },
  };
  const DEFAULT_VIEWER_PREFS = {
    lightingPreset: "neutral",
    exposure: 0.92,
    shadowIntensity: 0.35,
    shadowSoftness: 1,
    toneMapping: "neutral",
    environment: "neutral",
    showSkybox: false,
    fieldOfView: null,
    autoRotateSpeed: 30,
    animationSpeed: 1,
    animationLoopMode: "repeat",
    captureFormat: "image/png",
    captureQuality: 0.82,
    captureIdealAspect: false,
    animationCaptureFps: ANIMATION_CAPTURE_TARGET_FPS,
    animationCaptureMaxFrames: ANIMATION_CAPTURE_MAX_FRAMES,
    stageBackground: "warm",
    stageGrid: false,
    arPlacement: "floor",
    arScale: "auto",
  };
  const DEFAULT_CONFIG = {
    repoOwner: "RSDWArchive",
    repoName: "RSDWModel",
    repoBranch: "main",
    datasetVersion: "0.11.2.2",
    assetBaseUrl: "auto",
  };

  const els = {
    search: document.getElementById("model-search"),
    landing: document.getElementById("landing"),
    homeStatus: document.getElementById("home-status"),
    statTotal: document.getElementById("stat-total"),
    statVersion: document.getElementById("stat-version"),
    resultStatus: document.getElementById("result-status"),
    results: document.getElementById("results"),
    resultsFooter: document.getElementById("results-footer"),
    loadMoreResults: document.getElementById("load-more-results"),
    viewerLayout: document.getElementById("viewer-layout"),
    viewerInspector: document.getElementById("viewer-inspector"),
    inspectorSummary: document.getElementById("inspector-summary"),
    inspectorTabs: Array.from(document.querySelectorAll("[data-inspector-tab]")),
    inspectorPanels: Array.from(document.querySelectorAll("[data-inspector-panel]")),
    selectedTitle: document.getElementById("selected-title"),
    selectedPath: document.getElementById("selected-path"),
    warning: document.getElementById("missing-warning"),
    modelStage: document.getElementById("model-stage"),
    viewer: document.getElementById("model-viewer"),
    variantPanel: document.getElementById("variant-panel"),
    variantSelect: document.getElementById("model-variant-select"),
    materialEmpty: document.getElementById("material-empty"),
    materialPanel: document.getElementById("material-panel"),
    materialSelect: document.getElementById("material-select"),
    materialScope: document.getElementById("material-scope"),
    materialBaseColor: document.getElementById("material-base-color"),
    materialRoughness: document.getElementById("material-roughness"),
    materialRoughnessValue: document.getElementById("material-roughness-value"),
    materialMetallic: document.getElementById("material-metallic"),
    materialMetallicValue: document.getElementById("material-metallic-value"),
    materialBaseTexture: document.getElementById("material-base-texture"),
    materialStatus: document.getElementById("material-status"),
    resetMaterial: document.getElementById("reset-material"),
    resetAllMaterials: document.getElementById("reset-all-materials"),
    animationPanel: document.getElementById("animation-panel"),
    animationEmpty: document.getElementById("animation-empty"),
    animationFilter: document.getElementById("model-animation-filter"),
    animationSelect: document.getElementById("model-animation-select"),
    animationPrev: document.getElementById("animation-prev"),
    animationPlay: document.getElementById("animation-play-toggle"),
    animationRestart: document.getElementById("animation-restart"),
    animationNext: document.getElementById("animation-next"),
    animationStepBack: document.getElementById("animation-step-back"),
    animationStepForward: document.getElementById("animation-step-forward"),
    animationScrub: document.getElementById("animation-scrub"),
    animationTime: document.getElementById("animation-time"),
    animationSpeed: document.getElementById("animation-speed"),
    animationLoopMode: document.getElementById("animation-loop-mode"),
    captureAnimation: document.getElementById("capture-animation-webp"),
    captureAnimationPanel: document.getElementById("capture-animation-webp-panel"),
    controlsToggle: document.getElementById("viewer-controls-toggle"),
    viewerControls: document.getElementById("viewer-controls"),
    lightingPreset: document.getElementById("lighting-preset"),
    lightingExposure: document.getElementById("lighting-exposure"),
    lightingExposureValue: document.getElementById("lighting-exposure-value"),
    lightingShadow: document.getElementById("lighting-shadow"),
    lightingShadowValue: document.getElementById("lighting-shadow-value"),
    lightingShadowSoftness: document.getElementById("lighting-shadow-softness"),
    lightingShadowSoftnessValue: document.getElementById("lighting-shadow-softness-value"),
    lightingToneMapping: document.getElementById("lighting-tone-mapping"),
    lightingEnvironment: document.getElementById("lighting-environment"),
    lightingEnvironmentUpload: document.getElementById("lighting-environment-upload"),
    lightingShowSkybox: document.getElementById("lighting-show-skybox"),
    clearUploadedEnvironment: document.getElementById("clear-uploaded-environment"),
    lightingEnvironmentStatus: document.getElementById("lighting-environment-status"),
    resetLighting: document.getElementById("reset-lighting"),
    cameraFov: document.getElementById("camera-fov"),
    cameraFovValue: document.getElementById("camera-fov-value"),
    autoRotateSpeed: document.getElementById("auto-rotate-speed"),
    autoRotateSpeedValue: document.getElementById("auto-rotate-speed-value"),
    stageBackground: document.getElementById("stage-background"),
    stageGrid: document.getElementById("stage-grid"),
    fitCamera: document.getElementById("fit-camera"),
    saveCameraView: document.getElementById("save-camera-view"),
    loadCameraView: document.getElementById("load-camera-view"),
    captureFormat: document.getElementById("capture-format"),
    captureQuality: document.getElementById("capture-quality"),
    captureQualityValue: document.getElementById("capture-quality-value"),
    captureIdealAspect: document.getElementById("capture-ideal-aspect"),
    animationCaptureFps: document.getElementById("animation-capture-fps"),
    animationCaptureFpsValue: document.getElementById("animation-capture-fps-value"),
    animationCaptureMaxFrames: document.getElementById("animation-capture-max-frames"),
    animationCaptureMaxFramesValue: document.getElementById("animation-capture-max-frames-value"),
    arPlacement: document.getElementById("ar-placement"),
    arScale: document.getElementById("ar-scale"),
    activateAr: document.getElementById("activate-ar"),
    arStatus: document.getElementById("ar-status"),
    resetViewerPreferences: document.getElementById("reset-viewer-preferences"),
    loadProgress: document.getElementById("load-progress"),
    autoRotate: document.getElementById("auto-rotate-toggle"),
    resetCamera: document.getElementById("reset-camera"),
    saveScreenshot: document.getElementById("save-screenshot"),
    saveScreenshotPanel: document.getElementById("save-screenshot-panel"),
    openRaw: document.getElementById("open-raw"),
    openGithub: document.getElementById("open-github"),
    downloadModel: document.getElementById("download-model"),
    downloadDialog: document.getElementById("download-dialog"),
    downloadBackdrop: document.getElementById("download-dialog-backdrop"),
    downloadClose: document.getElementById("download-dialog-close"),
    downloadGlb: document.getElementById("download-glb"),
    downloadStl: document.getElementById("download-stl"),
    downloadStatus: document.getElementById("download-status"),
    copyLink: document.getElementById("copy-link"),
  };

  let config = { ...DEFAULT_CONFIG };
  let models = [];
  let variantsByModel = {};
  let animationsByModel = {};
  let activeKind = "all";
  let selectedModel = null;
  let selectedVariant = null;
  let selectedAnimation = null;
  let selectedButton = null;
  let debounceTimer = null;
  let visibleResultCount = RESULTS_PAGE_SIZE;
  let animationFilterText = "";
  let isScrubbingAnimation = false;
  let viewerIsLoading = false;
  let activeInspectorTab = "model";
  let viewerPrefs = loadViewerPreferences();
  let selectedMaterialIndex = 0;
  let materialBaselines = [];
  let uploadedMaterialTextureUrl = null;
  let uploadedEnvironmentUrl = null;
  let isExportingModel = false;

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
      return `../${encodePath(config.datasetVersion)}/WebAssets`;
    }
    return `${rawRepoBase()}/${encodePath(config.datasetVersion)}/WebAssets`;
  }

  function modelGltfPath(model, variant = selectedVariant, animation = selectedAnimation) {
    if (animation && animation.gltfPath && !variant) return animation.gltfPath;
    return (variant && variant.gltfPath) || model.gltfPath;
  }

  function modelRawUrl(model, variant = selectedVariant, animation = selectedAnimation) {
    return `${webAssetBase()}/${encodePath(modelGltfPath(model, variant, animation))}`;
  }

  function modelGithubUrl(model, variant = selectedVariant, animation = selectedAnimation) {
    return `${githubRepoBase()}/${encodePath(config.datasetVersion)}/WebAssets/${encodePath(modelGltfPath(model, variant, animation))}`;
  }

  function modelHash(model, variant = selectedVariant, animation = selectedAnimation) {
    const params = new URLSearchParams();
    params.set("model", model.id);
    if (variant && variant.id) params.set("variant", variant.id);
    if (animation && animation.id && !variant) params.set("animation", animation.id);
    return params.toString();
  }

  function parseHash() {
    const raw = window.location.hash.replace(/^#/, "");
    const params = new URLSearchParams(raw);
    return {
      modelId: params.get("model"),
      variantId: params.get("variant"),
      animationId: params.get("animation"),
    };
  }

  function clampNumber(value, min, max, fallback) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.min(max, Math.max(min, number));
  }

  function readStoredJson(key) {
    try {
      const raw = window.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  function writeStorage(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // Local storage can be unavailable in private browsing or strict browser modes.
    }
  }

  function removeStorage(key) {
    try {
      window.localStorage.removeItem(key);
    } catch {
      // Local storage can be unavailable in private browsing or strict browser modes.
    }
  }

  function loadViewerPreferences() {
    const stored = readStoredJson(VIEWER_PREFS_STORAGE_KEY) || {};
    return {
      lightingPreset: ["custom", ...Object.keys(LIGHTING_PRESETS)].includes(stored.lightingPreset)
        ? stored.lightingPreset
        : DEFAULT_VIEWER_PREFS.lightingPreset,
      exposure: clampNumber(stored.exposure, 0.2, 2, DEFAULT_VIEWER_PREFS.exposure),
      shadowIntensity: clampNumber(stored.shadowIntensity, 0, 2, DEFAULT_VIEWER_PREFS.shadowIntensity),
      shadowSoftness: clampNumber(stored.shadowSoftness, 0, 1, DEFAULT_VIEWER_PREFS.shadowSoftness),
      toneMapping: ["neutral", "aces", "agx", "cineon", "reinhard", "linear", "none"].includes(stored.toneMapping)
        ? stored.toneMapping
        : DEFAULT_VIEWER_PREFS.toneMapping,
      environment: ["neutral", "legacy"].includes(stored.environment)
        ? stored.environment
        : DEFAULT_VIEWER_PREFS.environment,
      showSkybox: Boolean(stored.showSkybox),
      fieldOfView: stored.fieldOfView === null || stored.fieldOfView === undefined
        ? null
        : clampNumber(stored.fieldOfView, 15, 70, null),
      autoRotateSpeed: clampNumber(stored.autoRotateSpeed, 5, 90, DEFAULT_VIEWER_PREFS.autoRotateSpeed),
      animationSpeed: clampNumber(stored.animationSpeed, 0.25, 2, DEFAULT_VIEWER_PREFS.animationSpeed),
      animationLoopMode: ["repeat", "once", "pingpong"].includes(stored.animationLoopMode)
        ? stored.animationLoopMode
        : DEFAULT_VIEWER_PREFS.animationLoopMode,
      captureFormat: ["image/png", "image/webp"].includes(stored.captureFormat)
        ? stored.captureFormat
        : DEFAULT_VIEWER_PREFS.captureFormat,
      captureQuality: clampNumber(stored.captureQuality, 0.5, 1, DEFAULT_VIEWER_PREFS.captureQuality),
      captureIdealAspect: Boolean(stored.captureIdealAspect),
      animationCaptureFps: clampNumber(stored.animationCaptureFps, 8, 60, DEFAULT_VIEWER_PREFS.animationCaptureFps),
      animationCaptureMaxFrames: clampNumber(stored.animationCaptureMaxFrames, 60, 900, DEFAULT_VIEWER_PREFS.animationCaptureMaxFrames),
      stageBackground: ["warm", "neutral", "flat", "transparent"].includes(stored.stageBackground)
        ? stored.stageBackground
        : DEFAULT_VIEWER_PREFS.stageBackground,
      stageGrid: Boolean(stored.stageGrid),
      arPlacement: ["floor", "wall"].includes(stored.arPlacement) ? stored.arPlacement : DEFAULT_VIEWER_PREFS.arPlacement,
      arScale: ["auto", "fixed"].includes(stored.arScale) ? stored.arScale : DEFAULT_VIEWER_PREFS.arScale,
    };
  }

  function saveViewerPreferences() {
    writeStorage(VIEWER_PREFS_STORAGE_KEY, JSON.stringify(viewerPrefs));
  }

  function setViewerControlsOpen(open) {
    els.viewerInspector.hidden = !open;
    els.viewerLayout.classList.toggle("is-inspector-hidden", !open);
    els.controlsToggle.setAttribute("aria-expanded", String(open));
    els.controlsToggle.textContent = open ? "Hide Controls" : "Controls";
    writeStorage(VIEWER_CONTROLS_STORAGE_KEY, open ? "1" : "0");
  }

  function restoreViewerControlsOpen() {
    try {
      setViewerControlsOpen(window.localStorage.getItem(VIEWER_CONTROLS_STORAGE_KEY) !== "0");
    } catch {
      setViewerControlsOpen(true);
    }
  }

  function setInspectorTab(tabName) {
    const requested = tabName || "model";
    const tabExists = els.inspectorTabs.some((tab) => tab.dataset.inspectorTab === requested);
    const nextTab = tabExists ? requested : "model";
    activeInspectorTab = nextTab;
    for (const tab of els.inspectorTabs) {
      const isActive = tab.dataset.inspectorTab === nextTab;
      tab.classList.toggle("is-active", isActive);
      tab.setAttribute("aria-selected", String(isActive));
    }
    for (const panel of els.inspectorPanels) {
      const isActive = panel.dataset.inspectorPanel === nextTab;
      panel.hidden = !isActive;
      panel.classList.toggle("is-active", isActive);
    }
    writeStorage(VIEWER_TAB_STORAGE_KEY, nextTab);
  }

  function restoreInspectorTab() {
    try {
      setInspectorTab(window.localStorage.getItem(VIEWER_TAB_STORAGE_KEY) || "model");
    } catch {
      setInspectorTab("model");
    }
  }

  function formatDecimal(value, digits = 2) {
    return Number(value).toFixed(digits);
  }

  function screenshotButtons() {
    return [els.saveScreenshot, els.saveScreenshotPanel].filter(Boolean);
  }

  function animationCaptureButtons() {
    return [els.captureAnimation, els.captureAnimationPanel].filter(Boolean);
  }

  function setScreenshotButtons(disabled, text) {
    for (const button of screenshotButtons()) {
      button.disabled = disabled;
      if (text) button.textContent = text;
    }
  }

  function setAnimationCaptureButtons(disabled, text) {
    for (const button of animationCaptureButtons()) {
      button.disabled = disabled;
      if (text) button.textContent = text;
    }
  }

  function renderViewerPreferenceInputs() {
    els.lightingPreset.value = viewerPrefs.lightingPreset;
    els.lightingExposure.value = String(viewerPrefs.exposure);
    els.lightingExposureValue.textContent = formatDecimal(viewerPrefs.exposure);
    els.lightingShadow.value = String(viewerPrefs.shadowIntensity);
    els.lightingShadowValue.textContent = formatDecimal(viewerPrefs.shadowIntensity);
    els.lightingShadowSoftness.value = String(viewerPrefs.shadowSoftness);
    els.lightingShadowSoftnessValue.textContent = formatDecimal(viewerPrefs.shadowSoftness);
    els.lightingToneMapping.value = viewerPrefs.toneMapping;
    els.lightingEnvironment.value = viewerPrefs.environment;
    els.lightingShowSkybox.checked = viewerPrefs.showSkybox;
    els.clearUploadedEnvironment.disabled = !uploadedEnvironmentUrl;
    els.lightingEnvironmentStatus.textContent = uploadedEnvironmentUrl
      ? "Using a local environment for this browser session."
      : "Local uploads stay in this browser session.";
    els.cameraFov.value = String(viewerPrefs.fieldOfView || DEFAULT_FIELD_OF_VIEW);
    els.cameraFovValue.textContent = viewerPrefs.fieldOfView ? `${Math.round(viewerPrefs.fieldOfView)} deg` : "Auto";
    els.autoRotateSpeed.value = String(viewerPrefs.autoRotateSpeed);
    els.autoRotateSpeedValue.textContent = `${Math.round(viewerPrefs.autoRotateSpeed)} deg/s`;
    els.animationSpeed.value = String(viewerPrefs.animationSpeed);
    els.animationLoopMode.value = viewerPrefs.animationLoopMode;
    els.captureFormat.value = viewerPrefs.captureFormat;
    els.captureQuality.value = String(viewerPrefs.captureQuality);
    els.captureQualityValue.textContent = formatDecimal(viewerPrefs.captureQuality);
    els.captureIdealAspect.checked = viewerPrefs.captureIdealAspect;
    els.animationCaptureFps.value = String(viewerPrefs.animationCaptureFps);
    els.animationCaptureFpsValue.textContent = String(Math.round(viewerPrefs.animationCaptureFps));
    els.animationCaptureMaxFrames.value = String(viewerPrefs.animationCaptureMaxFrames);
    els.animationCaptureMaxFramesValue.textContent = String(Math.round(viewerPrefs.animationCaptureMaxFrames));
    els.stageBackground.value = viewerPrefs.stageBackground;
    els.stageGrid.checked = viewerPrefs.stageGrid;
    els.arPlacement.value = viewerPrefs.arPlacement;
    els.arScale.value = viewerPrefs.arScale;
  }

  function applyViewerPreferences() {
    const environmentImage = uploadedEnvironmentUrl || viewerPrefs.environment;
    els.viewer.setAttribute("exposure", formatDecimal(viewerPrefs.exposure));
    els.viewer.setAttribute("shadow-intensity", formatDecimal(viewerPrefs.shadowIntensity));
    els.viewer.setAttribute("shadow-softness", formatDecimal(viewerPrefs.shadowSoftness));
    els.viewer.setAttribute("tone-mapping", viewerPrefs.toneMapping);
    els.viewer.setAttribute("environment-image", environmentImage);
    if (viewerPrefs.showSkybox && uploadedEnvironmentUrl) {
      els.viewer.setAttribute("skybox-image", uploadedEnvironmentUrl);
    } else {
      els.viewer.removeAttribute("skybox-image");
    }
    els.viewer.setAttribute("rotation-per-second", `${Math.round(viewerPrefs.autoRotateSpeed)}deg`);
    els.viewer.setAttribute("ar-placement", viewerPrefs.arPlacement);
    els.viewer.setAttribute("ar-scale", viewerPrefs.arScale);
    els.viewer.timeScale = viewerPrefs.animationSpeed;
    els.modelStage.classList.remove("stage-bg-warm", "stage-bg-neutral", "stage-bg-flat", "stage-bg-transparent");
    els.modelStage.classList.add(`stage-bg-${viewerPrefs.stageBackground}`);
    els.modelStage.classList.toggle("has-stage-grid", viewerPrefs.stageGrid);
    if (viewerPrefs.fieldOfView) {
      els.viewer.setAttribute("field-of-view", `${Math.round(viewerPrefs.fieldOfView)}deg`);
    } else {
      els.viewer.removeAttribute("field-of-view");
    }
  }

  function updateViewerPreference(key, value) {
    updateViewerPreferences({ [key]: value });
  }

  function updateViewerPreferences(patch) {
    viewerPrefs = { ...viewerPrefs, ...patch };
    renderViewerPreferenceInputs();
    applyViewerPreferences();
    saveViewerPreferences();
  }

  function updateCustomLightingPreference(key, value) {
    updateViewerPreferences({ lightingPreset: "custom", [key]: value });
  }

  function applyLightingPreset(name) {
    const preset = LIGHTING_PRESETS[name];
    if (!preset) {
      updateViewerPreference("lightingPreset", "custom");
      return;
    }
    updateViewerPreferences({ lightingPreset: name, ...preset });
  }

  function revokeUploadedEnvironment() {
    if (uploadedEnvironmentUrl) {
      URL.revokeObjectURL(uploadedEnvironmentUrl);
      uploadedEnvironmentUrl = null;
    }
    els.lightingEnvironmentUpload.value = "";
    els.lightingEnvironmentStatus.textContent = "Local uploads stay in this browser session.";
    renderViewerPreferenceInputs();
    applyViewerPreferences();
  }

  function revokeUploadedMaterialTexture() {
    if (uploadedMaterialTextureUrl) {
      URL.revokeObjectURL(uploadedMaterialTextureUrl);
      uploadedMaterialTextureUrl = null;
    }
    els.materialBaseTexture.value = "";
  }

  function resetLightingPreferences() {
    if (uploadedEnvironmentUrl) {
      URL.revokeObjectURL(uploadedEnvironmentUrl);
      uploadedEnvironmentUrl = null;
    }
    els.lightingEnvironmentUpload.value = "";
    els.lightingEnvironmentStatus.textContent = "Local uploads stay in this browser session.";
    viewerPrefs = {
      ...viewerPrefs,
      lightingPreset: DEFAULT_VIEWER_PREFS.lightingPreset,
      exposure: DEFAULT_VIEWER_PREFS.exposure,
      shadowIntensity: DEFAULT_VIEWER_PREFS.shadowIntensity,
      shadowSoftness: DEFAULT_VIEWER_PREFS.shadowSoftness,
      toneMapping: DEFAULT_VIEWER_PREFS.toneMapping,
      environment: DEFAULT_VIEWER_PREFS.environment,
      showSkybox: DEFAULT_VIEWER_PREFS.showSkybox,
    };
    renderViewerPreferenceInputs();
    applyViewerPreferences();
    saveViewerPreferences();
  }

  function resetViewerPreferences() {
    if (uploadedEnvironmentUrl) {
      URL.revokeObjectURL(uploadedEnvironmentUrl);
      uploadedEnvironmentUrl = null;
    }
    if (els.lightingEnvironmentUpload) els.lightingEnvironmentUpload.value = "";
    revokeUploadedMaterialTexture();
    viewerPrefs = { ...DEFAULT_VIEWER_PREFS };
    removeStorage(VIEWER_PREFS_STORAGE_KEY);
    removeStorage(VIEWER_CONTROLS_STORAGE_KEY);
    removeStorage(VIEWER_TAB_STORAGE_KEY);
    renderViewerPreferenceInputs();
    applyViewerPreferences();
    setViewerControlsOpen(true);
    setInspectorTab("model");
  }

  function updateArStatus(message) {
    if (els.arStatus) els.arStatus.textContent = message;
  }

  function syncArStatus() {
    const arStatus = els.viewer.getAttribute("ar-status") || "not-presenting";
    const arTracking = els.viewer.getAttribute("ar-tracking") || "";
    if (arStatus === "session-started") {
      updateArStatus(arTracking === "not-tracking" ? "AR is active; move the device to find a surface." : "AR is active.");
    } else if (arStatus === "failed") {
      updateArStatus("AR could not start on this device.");
    } else if (els.viewer.canActivateAR) {
      updateArStatus("AR is ready on this device.");
    } else {
      updateArStatus("AR appears on supported mobile devices.");
    }
    els.activateAr.disabled = !selectedModel || !els.viewer.canActivateAR;
  }

  function setStatus(message) {
    els.resultStatus.textContent = message;
  }

  function updateLandingDensity() {
    els.landing.classList.toggle("is-compact", Boolean(els.search.value.trim() || selectedModel));
  }

  function normalizeText(value) {
    return String(value || "").toLowerCase();
  }

  function parseQuery(query) {
    const raw = normalizeText(query).trim().split(/\s+/).filter(Boolean);
    const positives = [];
    const negatives = [];
    for (const token of raw) {
      if (token.startsWith("-") && token.length > 1) {
        negatives.push(token.slice(1));
      } else if (!token.startsWith("-")) {
        positives.push(token);
      }
    }
    return { positives, negatives };
  }

  function scoreModel(model, positives, negatives) {
    const haystack = model.searchText;
    for (const token of negatives) {
      if (haystack.includes(token)) return -1;
    }
    for (const token of positives) {
      if (!haystack.includes(token)) return -1;
    }
    if (activeKind !== "all" && model.kind !== activeKind) return -1;
    if (positives.length === 0) {
      return Math.max(0, 80 - Math.floor(model.path.length * 0.02));
    }
    const query = positives.join(" ");
    let score = 0;
    const name = model.displayNameLower;
    const path = model.pathLower;
    if (name === query) score += 1500;
    else if (name.startsWith(query)) score += 1000;
    else if (path.endsWith(query)) score += 650;
    for (const token of positives) {
      if (name === token) score += 420;
      else if (name.startsWith(token)) score += 280;
      else if (name.includes(token)) score += 140;
      score += path.includes(`/${token}`) ? 70 : 20;
    }
    score += model.kind === "SM" ? 8 : 0;
    return score;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function pathHint(path) {
    const parts = String(path || "").split("/");
    if (parts.length <= 4) return path;
    return `${parts.slice(0, 2).join("/")}/.../${parts.slice(-3).join("/")}`;
  }

  function enrichModel(model) {
    const displayName = model.displayName || model.name || model.path;
    const path = model.path || "";
    const searchText = `${model.kind} ${displayName} ${model.name || ""} ${path}`.toLowerCase();
    return {
      ...model,
      displayName,
      displayNameLower: displayName.toLowerCase(),
      pathLower: path.toLowerCase(),
      searchText,
    };
  }

  function variantsForModel(model) {
    if (!model || !model.id) return [];
    const group = variantsByModel[model.id];
    return (group && Array.isArray(group.variants)) ? group.variants : [];
  }

  function variantById(model, id) {
    if (!id) return null;
    return variantsForModel(model).find((variant) => variant.id === id) || null;
  }

  function animationsForModel(model) {
    if (!model || !model.id) return [];
    const group = animationsByModel[model.id];
    const rows = (group && Array.isArray(group.animations)) ? group.animations : [];
    return rows.filter((row) => row && row.status === "success" && row.gltfPath);
  }

  function animationById(model, id) {
    if (!id) return null;
    return animationsForModel(model).find((animation) => animation.id === id) || null;
  }

  function animationSearchText(animation) {
    return [
      animation.id,
      animation.label,
      animation.name,
      animation.animationName,
      animation.packagePath,
    ].filter(Boolean).join(" ").toLowerCase();
  }

  function filteredAnimationsForModel(model = selectedModel) {
    const rows = animationsForModel(model);
    const query = animationFilterText.trim().toLowerCase();
    if (!query) return rows;
    return rows.filter((animation) => animationSearchText(animation).includes(query));
  }

  function formatTime(seconds) {
    const safeSeconds = Math.max(0, Number(seconds) || 0);
    const minutes = Math.floor(safeSeconds / 60);
    const remainingSeconds = Math.floor(safeSeconds % 60);
    return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
  }

  function currentAnimationDuration() {
    if (!selectedAnimation) return 0;
    const viewerDuration = Number(els.viewer.duration || 0);
    const indexedDuration = Number(selectedAnimation.duration_s || 0);
    return Number.isFinite(viewerDuration) && viewerDuration > 0 ? viewerDuration : indexedDuration;
  }

  function playSelectedAnimation() {
    if (!selectedAnimation || typeof els.viewer.play !== "function") return;
    els.viewer.timeScale = viewerPrefs.animationSpeed;
    if (viewerPrefs.animationLoopMode === "once") {
      els.viewer.play({ repetitions: 1, pingpong: false });
    } else if (viewerPrefs.animationLoopMode === "pingpong") {
      els.viewer.play({ repetitions: Infinity, pingpong: true });
    } else {
      els.viewer.play({ repetitions: Infinity, pingpong: false });
    }
  }

  function updateAnimationTimeline() {
    if (!els.animationScrub || !els.animationTime) return;
    const duration = currentAnimationDuration();
    const hasAnimation = Boolean(selectedAnimation && duration > 0);
    const currentTime = hasAnimation ? clampNumber(els.viewer.currentTime || 0, 0, duration, 0) : 0;
    els.animationScrub.disabled = !hasAnimation;
    els.animationScrub.max = hasAnimation ? String(duration) : "0";
    els.animationScrub.step = duration > 20 ? "0.05" : "0.01";
    if (!isScrubbingAnimation) els.animationScrub.value = String(currentTime);
    els.animationTime.textContent = `${formatTime(currentTime)} / ${formatTime(duration)}`;
  }

  function updateAnimationButtons() {
    const animations = animationsForModel(selectedModel);
    const hasAnimations = animations.length > 0 && !selectedVariant;
    const hasSelectedAnimation = Boolean(selectedAnimation);
    const viewerReady = !viewerIsLoading && Boolean(els.viewer.loaded);
    els.animationPrev.disabled = !hasAnimations;
    els.animationNext.disabled = !hasAnimations;
    els.animationPlay.disabled = !hasSelectedAnimation;
    els.animationRestart.disabled = !hasSelectedAnimation;
    els.animationStepBack.disabled = !hasSelectedAnimation;
    els.animationStepForward.disabled = !hasSelectedAnimation;
    els.animationLoopMode.disabled = !hasSelectedAnimation;
    setAnimationCaptureButtons(!hasSelectedAnimation || !viewerReady);
    els.animationSpeed.disabled = !hasSelectedAnimation;
    els.animationPlay.textContent = hasSelectedAnimation && !els.viewer.paused ? "Pause" : "Play";
    updateAnimationTimeline();
  }

  function setAnimationEmpty(message) {
    els.animationEmpty.hidden = !message;
    els.animationEmpty.textContent = message || "";
  }

  function selectAdjacentAnimation(direction) {
    if (!selectedModel || selectedVariant) return;
    const allRows = animationsForModel(selectedModel);
    if (!allRows.length) return;
    const filteredRows = filteredAnimationsForModel(selectedModel);
    const rows = filteredRows.length ? filteredRows : allRows;
    const currentIndex = selectedAnimation ? rows.findIndex((animation) => animation.id === selectedAnimation.id) : -1;
    const nextIndex = currentIndex < 0
      ? (direction > 0 ? 0 : rows.length - 1)
      : (currentIndex + direction + rows.length) % rows.length;
    const nextAnimation = rows[nextIndex];
    if (!nextAnimation) return;
    selectModel(selectedModel, { updateHash: true, animationId: nextAnimation.id });
  }

  function restartSelectedAnimation() {
    if (!selectedAnimation) return;
    els.viewer.currentTime = 0;
    playSelectedAnimation();
    els.animationPlay.textContent = "Pause";
    updateAnimationTimeline();
  }

  function stepSelectedAnimation(deltaSeconds) {
    if (!selectedAnimation) return;
    const duration = currentAnimationDuration();
    const nextTime = clampNumber(Number(els.viewer.currentTime || 0) + deltaSeconds, 0, duration, 0);
    els.viewer.currentTime = nextTime;
    updateAnimationTimeline();
  }

  function viewerMaterials() {
    return Array.from((els.viewer.model && els.viewer.model.materials) || []);
  }

  function materialLabel(material, index) {
    return material && material.name ? material.name : `Material ${index + 1}`;
  }

  function materialPbr(material) {
    return material && material.pbrMetallicRoughness ? material.pbrMetallicRoughness : null;
  }

  function normalizeColorFactor(value) {
    const source = Array.isArray(value) ? value : [1, 1, 1, 1];
    return [
      clampNumber(source[0], 0, 1, 1),
      clampNumber(source[1], 0, 1, 1),
      clampNumber(source[2], 0, 1, 1),
      clampNumber(source[3], 0, 1, 1),
    ];
  }

  function factorToHex(value) {
    const [r, g, b] = normalizeColorFactor(value);
    const channel = (number) => Math.round(number * 255).toString(16).padStart(2, "0");
    return `#${channel(r)}${channel(g)}${channel(b)}`;
  }

  function hexToFactor(hex, alpha = 1) {
    const normalized = /^#[0-9a-f]{6}$/i.test(hex) ? hex : "#ffffff";
    return [
      parseInt(normalized.slice(1, 3), 16) / 255,
      parseInt(normalized.slice(3, 5), 16) / 255,
      parseInt(normalized.slice(5, 7), 16) / 255,
      alpha,
    ];
  }

  function selectedMaterial() {
    return viewerMaterials()[selectedMaterialIndex] || null;
  }

  function targetMaterials() {
    const materials = viewerMaterials();
    if (els.materialScope.value === "all") return materials;
    const material = selectedMaterial();
    return material ? [material] : [];
  }

  function setMaterialEmpty(message) {
    els.materialEmpty.hidden = !message;
    els.materialEmpty.textContent = message || "";
    els.materialPanel.hidden = Boolean(message);
    if (message) {
      els.resetMaterial.disabled = true;
      els.resetAllMaterials.disabled = true;
    }
  }

  function captureMaterialBaselines() {
    materialBaselines = viewerMaterials().map((material) => {
      const pbr = materialPbr(material);
      return {
        baseColorFactor: normalizeColorFactor(pbr && pbr.baseColorFactor),
        roughnessFactor: typeof (pbr && pbr.roughnessFactor) === "number" ? pbr.roughnessFactor : 1,
        metallicFactor: typeof (pbr && pbr.metallicFactor) === "number" ? pbr.metallicFactor : 0,
        baseColorTexture: pbr && pbr.baseColorTexture ? pbr.baseColorTexture.texture : null,
      };
    });
  }

  function syncMaterialInputsFromSelected() {
    const material = selectedMaterial();
    const pbr = materialPbr(material);
    const baseline = materialBaselines[selectedMaterialIndex] || {};
    const colorFactor = normalizeColorFactor(pbr && pbr.baseColorFactor);
    const roughness = typeof (pbr && pbr.roughnessFactor) === "number" ? pbr.roughnessFactor : baseline.roughnessFactor ?? 1;
    const metallic = typeof (pbr && pbr.metallicFactor) === "number" ? pbr.metallicFactor : baseline.metallicFactor ?? 0;
    els.materialBaseColor.value = factorToHex(colorFactor);
    els.materialRoughness.value = String(roughness);
    els.materialRoughnessValue.textContent = formatDecimal(roughness);
    els.materialMetallic.value = String(metallic);
    els.materialMetallicValue.textContent = formatDecimal(metallic);
  }

  function renderMaterialControls(message) {
    if (message) {
      selectedMaterialIndex = 0;
      materialBaselines = [];
      els.materialSelect.textContent = "";
      setMaterialEmpty(message);
      return;
    }
    const materials = viewerMaterials();
    if (!materials.length) {
      setMaterialEmpty("No editable glTF materials were exposed by this model.");
      return;
    }
    setMaterialEmpty("");
    els.inspectorSummary.textContent = selectedModel ? selectedModel.displayName : "No model selected.";
    els.materialSelect.textContent = "";
    materials.forEach((material, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = materialLabel(material, index);
      els.materialSelect.appendChild(option);
    });
    selectedMaterialIndex = Math.min(selectedMaterialIndex, materials.length - 1);
    els.materialSelect.value = String(selectedMaterialIndex);
    els.resetMaterial.disabled = false;
    els.resetAllMaterials.disabled = false;
    els.materialStatus.textContent = "Edits are temporary and reset when the page reloads or another model loads.";
    syncMaterialInputsFromSelected();
  }

  function applyMaterialFactors() {
    const color = hexToFactor(els.materialBaseColor.value, normalizeColorFactor(materialPbr(selectedMaterial())?.baseColorFactor)[3]);
    const roughness = clampNumber(els.materialRoughness.value, 0, 1, 1);
    const metallic = clampNumber(els.materialMetallic.value, 0, 1, 0);
    els.materialRoughnessValue.textContent = formatDecimal(roughness);
    els.materialMetallicValue.textContent = formatDecimal(metallic);
    for (const material of targetMaterials()) {
      const pbr = materialPbr(material);
      if (!pbr) continue;
      if (typeof pbr.setBaseColorFactor === "function") pbr.setBaseColorFactor(color);
      if (typeof pbr.setRoughnessFactor === "function") pbr.setRoughnessFactor(roughness);
      if (typeof pbr.setMetallicFactor === "function") pbr.setMetallicFactor(metallic);
    }
    els.materialStatus.textContent = els.materialScope.value === "all"
      ? "Applied to all materials."
      : "Applied to selected material.";
  }

  async function applyUploadedBaseTexture(file) {
    if (!file || !selectedModel) return;
    if (typeof els.viewer.createTexture !== "function") {
      els.materialStatus.textContent = "Texture upload is not available in this browser.";
      return;
    }
    revokeUploadedMaterialTexture();
    uploadedMaterialTextureUrl = URL.createObjectURL(file);
    try {
      const texture = await els.viewer.createTexture(uploadedMaterialTextureUrl);
      let appliedCount = 0;
      for (const material of targetMaterials()) {
        const pbr = materialPbr(material);
        const textureInfo = pbr && pbr.baseColorTexture;
        if (textureInfo && typeof textureInfo.setTexture === "function") {
          textureInfo.setTexture(texture);
          appliedCount += 1;
        }
      }
      els.materialStatus.textContent = appliedCount
        ? `Applied ${file.name} to ${appliedCount} material${appliedCount === 1 ? "" : "s"}.`
        : "The selected material has no replaceable base texture slot.";
    } catch (error) {
      els.materialStatus.textContent = "Texture upload failed for this image.";
      console.error(error);
    }
  }

  function resetMaterialAt(index) {
    const material = viewerMaterials()[index];
    const baseline = materialBaselines[index];
    const pbr = materialPbr(material);
    if (!pbr || !baseline) return;
    if (typeof pbr.setBaseColorFactor === "function") pbr.setBaseColorFactor(baseline.baseColorFactor);
    if (typeof pbr.setRoughnessFactor === "function") pbr.setRoughnessFactor(baseline.roughnessFactor);
    if (typeof pbr.setMetallicFactor === "function") pbr.setMetallicFactor(baseline.metallicFactor);
    const textureInfo = pbr.baseColorTexture;
    if (baseline.baseColorTexture && textureInfo && typeof textureInfo.setTexture === "function") {
      textureInfo.setTexture(baseline.baseColorTexture);
    }
  }

  function resetSelectedMaterial() {
    resetMaterialAt(selectedMaterialIndex);
    syncMaterialInputsFromSelected();
    els.materialStatus.textContent = "Selected material reset.";
  }

  function resetAllMaterials() {
    viewerMaterials().forEach((_, index) => resetMaterialAt(index));
    revokeUploadedMaterialTexture();
    syncMaterialInputsFromSelected();
    els.materialStatus.textContent = "All materials reset.";
  }

  function fitCameraToModel() {
    if (!selectedModel) return;
    if (typeof els.viewer.updateFraming === "function") {
      els.viewer.updateFraming();
    }
    els.viewer.cameraOrbit = "0deg 75deg auto";
    if (typeof els.viewer.jumpCameraToGoal === "function") {
      els.viewer.jumpCameraToGoal();
    }
  }

  function saveCameraView() {
    if (!selectedModel) return;
    const view = {
      cameraOrbit: typeof els.viewer.getCameraOrbit === "function" ? els.viewer.getCameraOrbit().toString() : els.viewer.cameraOrbit,
      cameraTarget: typeof els.viewer.getCameraTarget === "function" ? els.viewer.getCameraTarget().toString() : els.viewer.cameraTarget,
      fieldOfView: typeof els.viewer.getFieldOfView === "function" ? `${els.viewer.getFieldOfView()}deg` : els.viewer.getAttribute("field-of-view"),
    };
    writeStorage(CUSTOM_CAMERA_STORAGE_KEY, JSON.stringify(view));
    els.loadCameraView.disabled = false;
    els.saveCameraView.textContent = "Saved";
    window.setTimeout(() => {
      els.saveCameraView.textContent = "Save View";
    }, 1200);
  }

  function loadCameraView() {
    const view = readStoredJson(CUSTOM_CAMERA_STORAGE_KEY);
    if (!view) return;
    if (view.cameraOrbit) els.viewer.cameraOrbit = view.cameraOrbit;
    if (view.cameraTarget) els.viewer.cameraTarget = view.cameraTarget;
    if (view.fieldOfView) els.viewer.setAttribute("field-of-view", view.fieldOfView);
    if (typeof els.viewer.jumpCameraToGoal === "function") {
      els.viewer.jumpCameraToGoal();
    }
  }

  function attachVariantSearch(model) {
    const variants = variantsForModel(model);
    if (!variants.length) return model;
    const variantTerms = variants
      .map((variant) => `${variant.label || ""} ${variant.slot || ""} ${variant.sex || ""} ${variant.meshDataPath || ""}`)
      .join(" ");
    return {
      ...model,
      variantCount: variants.length,
      searchText: `${model.searchText} ${variantTerms}`.toLowerCase(),
    };
  }

  function attachAnimationSearch(model) {
    const animations = animationsForModel(model);
    if (!animations.length) return model;
    const animationTerms = animations
      .map((animation) => `${animation.label || ""} ${animation.name || ""} ${animation.packagePath || ""}`)
      .join(" ");
    return {
      ...model,
      animationCount: animations.length,
      searchText: `${model.searchText} ${animationTerms}`.toLowerCase(),
    };
  }

  function allFilteredModels() {
    const { positives, negatives } = parseQuery(els.search.value);
    return models
      .map((model) => ({ model, score: scoreModel(model, positives, negatives) }))
      .filter((row) => row.score >= 0)
      .sort((a, b) => b.score - a.score || a.model.displayName.localeCompare(b.model.displayName))
      .map((row) => row.model);
  }

  function resetVisibleResults() {
    visibleResultCount = RESULTS_PAGE_SIZE;
  }

  function updateLoadMoreState(totalCount, shownCount) {
    if (!els.resultsFooter || !els.loadMoreResults) return;
    const remaining = Math.max(0, totalCount - shownCount);
    els.resultsFooter.hidden = remaining <= 0;
    els.loadMoreResults.hidden = remaining <= 0;
    if (remaining > 0) {
      const nextCount = Math.min(RESULTS_PAGE_SIZE, remaining);
      els.loadMoreResults.textContent = `Load ${nextCount.toLocaleString()} More`;
      els.loadMoreResults.title = `${remaining.toLocaleString()} more matching models available`;
    }
  }

  function renderResults() {
    const allRows = allFilteredModels();
    const rows = allRows.slice(0, visibleResultCount);
    els.results.textContent = "";
    selectedButton = null;
    const query = els.search.value.trim();
    const totalCount = allRows.length;
    const shownCount = rows.length;
    const totalLabel = `${totalCount.toLocaleString()} result${totalCount === 1 ? "" : "s"}`;
    const shownLabel = totalCount > shownCount
      ? `, showing ${shownCount.toLocaleString()}`
      : "";
    setStatus(`${totalLabel}${shownLabel}${query || activeKind !== "all" ? "" : ` of ${models.length.toLocaleString()}`}`);
    for (const model of rows) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "result-button";
      if (selectedModel && selectedModel.id === model.id) {
        button.classList.add("is-active");
        selectedButton = button;
      }
      button.innerHTML = `
        <span class="result-main">
          <span class="pill">${escapeHtml(model.kind)}</span>
          <span class="result-name">${escapeHtml(model.displayName)}</span>
        </span>
        <span class="result-path">${escapeHtml(pathHint(model.path))}</span>
        <span class="result-meta">
          ${model.optimizedTextureCount || 0} texture${model.optimizedTextureCount === 1 ? "" : "s"}
          ${model.variantCount ? `- ${model.variantCount} variant${model.variantCount === 1 ? "" : "s"}` : ""}
          ${model.animationCount ? `- ${model.animationCount} animation${model.animationCount === 1 ? "" : "s"}` : ""}
          ${model.missingTextureCount ? `<span class="warn">- ${model.missingTextureCount} missing</span>` : ""}
        </span>
      `;
      button.addEventListener("click", () => selectModel(model, { updateHash: true }));
      li.appendChild(button);
      els.results.appendChild(li);
    }
    if (rows.length === 0) {
      const li = document.createElement("li");
      li.className = "result-empty";
      li.textContent = "No matching models.";
      els.results.appendChild(li);
    }
    updateLoadMoreState(totalCount, shownCount);
  }

  function setActionLink(anchor, href) {
    anchor.href = href || "#";
    anchor.setAttribute("aria-disabled", href ? "false" : "true");
    anchor.tabIndex = href ? 0 : -1;
  }

  function screenshotExtension(mimeType = viewerPrefs.captureFormat) {
    return mimeType === "image/webp" ? "webp" : "png";
  }

  function screenshotFileName(model, mimeType = viewerPrefs.captureFormat) {
    const variantSuffix = selectedVariant && selectedVariant.label ? `_${selectedVariant.label}` : "";
    const animationSuffix = selectedAnimation && selectedAnimation.label ? `_${selectedAnimation.label}` : "";
    const baseName = `${model.displayName || model.name || "RSDWModel"}${variantSuffix}${animationSuffix}`
      .replace(/\.[^.]+$/, "")
      .replace(/[^a-z0-9_-]+/gi, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 96);
    return `${baseName || "RSDWModel"}_${model.kind || "Model"}.${screenshotExtension(mimeType)}`;
  }

  function animationCaptureFileName(model) {
    const animationSuffix = selectedAnimation && selectedAnimation.label ? `_${selectedAnimation.label}` : "";
    const baseName = `${model.displayName || model.name || "RSDWModel"}${animationSuffix}`
      .replace(/\.[^.]+$/, "")
      .replace(/[^a-z0-9_-]+/gi, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 96);
    return `${baseName || "RSDWModel"}_${model.kind || "Model"}_animation.webp`;
  }

  function modelExportBaseName(model = selectedModel) {
    const variantSuffix = selectedVariant && selectedVariant.label ? `_${selectedVariant.label}` : "";
    const animationSuffix = selectedAnimation && selectedAnimation.label ? `_${selectedAnimation.label}` : "";
    return `${(model && (model.displayName || model.name)) || "RSDWModel"}${variantSuffix}${animationSuffix}`
      .replace(/\.[^.]+$/, "")
      .replace(/[^a-z0-9_-]+/gi, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 96) || "RSDWModel";
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

  function setDownloadButtons(disabled, message) {
    const shouldDisable = disabled || !selectedModel;
    els.downloadModel.disabled = shouldDisable;
    els.downloadGlb.disabled = shouldDisable;
    els.downloadStl.disabled = shouldDisable;
    if (message) els.downloadStatus.textContent = message;
  }

  function setDownloadDialogOpen(open) {
    els.downloadDialog.hidden = !open;
    document.body.classList.toggle("modal-open", open);
    if (open) {
      els.downloadStatus.textContent = "Exports run locally in your browser.";
      window.setTimeout(() => els.downloadGlb.focus(), 0);
    } else {
      window.setTimeout(() => els.downloadModel.focus(), 0);
    }
  }

  function openDownloadDialog() {
    if (!selectedModel || els.downloadModel.disabled) return;
    setDownloadDialogOpen(true);
  }

  function closeDownloadDialog() {
    if (isExportingModel) return;
    setDownloadDialogOpen(false);
  }

  function exportedSceneToBlob(exported, mimeType) {
    if (exported instanceof Blob) return exported;
    if (exported instanceof ArrayBuffer) return new Blob([exported], { type: mimeType });
    if (ArrayBuffer.isView(exported)) return new Blob([exported], { type: mimeType });
    return new Blob([exported], { type: mimeType });
  }

  async function exportCurrentGlbBlob() {
    if (!selectedModel || typeof els.viewer.exportScene !== "function") {
      throw new Error("GLB export is not available in this browser.");
    }
    const exported = await els.viewer.exportScene({
      binary: true,
      trs: true,
      onlyVisible: true,
      maxTextureSize: Infinity,
      forcePowerOfTwoTextures: false,
      includeCustomExtensions: false,
      embedImages: true,
    });
    return exportedSceneToBlob(exported, "model/gltf-binary");
  }

  async function downloadCurrentGlb() {
    if (!selectedModel || isExportingModel) return;
    isExportingModel = true;
    setDownloadButtons(true, "Preparing GLB...");
    try {
      const blob = await exportCurrentGlbBlob();
      downloadBlob(blob, `${modelExportBaseName()}.glb`);
      setDownloadButtons(true, "GLB download started.");
      window.setTimeout(() => {
        if (selectedModel) setDownloadButtons(false, "Exports run locally in your browser.");
      }, 1200);
    } catch (error) {
      console.error(error);
      setDownloadButtons(false, "GLB export failed. Try again after the model finishes loading.");
    } finally {
      isExportingModel = false;
    }
  }

  async function downloadCurrentStl() {
    if (!selectedModel || isExportingModel) return;
    isExportingModel = true;
    setDownloadButtons(true, "Preparing scene for STL...");
    let glbUrl = null;
    try {
      const glbBlob = await exportCurrentGlbBlob();
      setDownloadButtons(true, "Converting geometry to STL...");
      const [{ GLTFLoader }, { STLExporter }] = await Promise.all([
        import("three/addons/loaders/GLTFLoader.js"),
        import("three/addons/exporters/STLExporter.js"),
      ]);
      glbUrl = URL.createObjectURL(glbBlob);
      const gltf = await new GLTFLoader().loadAsync(glbUrl);
      const stl = new STLExporter().parse(gltf.scene, { binary: true });
      const stlBlob = exportedSceneToBlob(stl, "model/stl");
      downloadBlob(stlBlob, `${modelExportBaseName()}.stl`);
      setDownloadButtons(true, "STL download started.");
      window.setTimeout(() => {
        if (selectedModel) setDownloadButtons(false, "STL is geometry-only; textures and materials are not included.");
      }, 1200);
    } catch (error) {
      console.error(error);
      setDownloadButtons(false, "STL export failed. This model may be too large or unsupported in this browser.");
    } finally {
      if (glbUrl) URL.revokeObjectURL(glbUrl);
      isExportingModel = false;
    }
  }

  function dataUrlToBytes(dataUrl) {
    const comma = dataUrl.indexOf(",");
    if (comma < 0) throw new Error("Invalid data URL.");
    const binary = window.atob(dataUrl.slice(comma + 1));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
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

  async function imageSizeFromDataUrl(dataUrl) {
    const image = new Image();
    image.decoding = "async";
    image.src = dataUrl;
    await image.decode();
    return { width: image.naturalWidth, height: image.naturalHeight };
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
      const payload = concatBytes([header, frame.payload]);
      chunks.push(makeRiffChunk("ANMF", payload));
    }

    const riffPayload = concatBytes([asciiBytes("WEBP"), ...chunks]);
    const out = new Uint8Array(8 + riffPayload.length);
    out.set(asciiBytes("RIFF"), 0);
    writeUint32(out, 4, riffPayload.length);
    out.set(riffPayload, 8);
    return new Blob([out], { type: "image/webp" });
  }

  function waitForViewerFrame() {
    return new Promise((resolve) => {
      window.requestAnimationFrame(() => window.requestAnimationFrame(resolve));
    });
  }

  async function captureCurrentAnimationWebp() {
    if (!selectedModel || !selectedAnimation || animationCaptureButtons().every((button) => button.disabled)) return;
    if (typeof els.viewer.toDataURL !== "function") {
      els.warning.hidden = false;
      els.warning.textContent = "Animation capture is not available in this browser.";
      return;
    }

    const duration = Number(els.viewer.duration || selectedAnimation.duration_s || 0);
    if (!Number.isFinite(duration) || duration <= 0) {
      els.warning.hidden = false;
      els.warning.textContent = "The selected animation does not report a capturable duration.";
      return;
    }

    const wasPaused = els.viewer.paused;
    const previousTime = Number(els.viewer.currentTime || 0);
    const previousText = els.captureAnimation.textContent;
    const frameRate = Math.max(
      4,
      Math.min(viewerPrefs.animationCaptureFps, Math.floor(viewerPrefs.animationCaptureMaxFrames / duration) || viewerPrefs.animationCaptureFps),
    );
    const frameCount = Math.max(2, Math.ceil(duration * frameRate));
    const frameDelayMs = Math.max(20, Math.round((duration * 1000) / frameCount));

    setAnimationCaptureButtons(true);
    els.animationPlay.disabled = true;
    setAnimationCaptureButtons(true, "Capturing 0%");
    try {
      els.viewer.pause();
      const frames = [];
      let size = null;
      for (let frameIndex = 0; frameIndex < frameCount; frameIndex += 1) {
        els.viewer.currentTime = (duration * frameIndex) / frameCount;
        await waitForViewerFrame();
        const dataUrl = await Promise.resolve(els.viewer.toDataURL("image/webp", viewerPrefs.captureQuality));
        if (!size) size = await imageSizeFromDataUrl(dataUrl);
        frames.push(framePayloadFromWebp(dataUrl));
        setAnimationCaptureButtons(true, `Capturing ${Math.round(((frameIndex + 1) / frameCount) * 100)}%`);
      }
      const blob = makeAnimatedWebp(frames, size.width, size.height, frameDelayMs);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = animationCaptureFileName(selectedModel);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 4000);
      setAnimationCaptureButtons(true, "Captured");
      window.setTimeout(() => {
        if (selectedAnimation) setAnimationCaptureButtons(false, previousText);
      }, 1200);
    } catch (error) {
      els.warning.hidden = false;
      els.warning.textContent = "Animation capture failed. Try again after the animation finishes loading.";
      setAnimationCaptureButtons(false, previousText);
      console.error(error);
    } finally {
      els.viewer.currentTime = previousTime;
      await waitForViewerFrame();
      if (!wasPaused && typeof els.viewer.play === "function") {
        playSelectedAnimation();
      } else {
        els.viewer.pause();
      }
      els.animationPlay.disabled = !selectedAnimation;
      els.animationPlay.textContent = selectedAnimation && !els.viewer.paused ? "Pause" : "Play";
      updateAnimationButtons();
    }
  }

  async function captureViewerBlob(mimeType) {
    if (typeof els.viewer.toBlob === "function") {
      return els.viewer.toBlob({
        mimeType,
        qualityArgument: viewerPrefs.captureQuality,
        idealAspect: viewerPrefs.captureIdealAspect,
      });
    }
    const dataUrl = await Promise.resolve(els.viewer.toDataURL(mimeType, viewerPrefs.captureQuality));
    const response = await fetch(dataUrl);
    return response.blob();
  }

  async function saveCurrentScreenshot() {
    if (!selectedModel || screenshotButtons().every((button) => button.disabled)) return;
    if (typeof els.viewer.toDataURL !== "function" && typeof els.viewer.toBlob !== "function") {
      els.warning.hidden = false;
      els.warning.textContent = "Screenshot capture is not available in this browser.";
      return;
    }

    const previousText = els.saveScreenshot.textContent;
    const mimeType = viewerPrefs.captureFormat;
    setScreenshotButtons(true, "Saving");
    try {
      const blob = await captureViewerBlob(mimeType);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = screenshotFileName(selectedModel, mimeType);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 4000);
      setScreenshotButtons(true, "Saved");
      window.setTimeout(() => {
        if (selectedModel) {
          setScreenshotButtons(!els.viewer.loaded, previousText);
        }
      }, 1200);
    } catch (error) {
      els.warning.hidden = false;
      els.warning.textContent = "Screenshot capture failed. Try again after the model finishes loading.";
      setScreenshotButtons(!els.viewer.loaded, previousText);
      console.error(error);
    }
  }

  function renderVariantPanel() {
    if (!selectedModel) {
      els.variantPanel.hidden = true;
      els.variantSelect.textContent = "";
      return;
    }
    const variants = variantsForModel(selectedModel);
    if (!variants.length) {
      els.variantPanel.hidden = true;
      els.variantSelect.textContent = "";
      selectedVariant = null;
      return;
    }
    els.variantPanel.hidden = false;
    els.variantSelect.textContent = "";
    const defaultOption = document.createElement("option");
    defaultOption.value = "";
    defaultOption.textContent = "Default Material";
    els.variantSelect.appendChild(defaultOption);
    for (const variant of variants) {
      const option = document.createElement("option");
      option.value = variant.id;
      option.textContent = variant.label || variant.id;
      els.variantSelect.appendChild(option);
    }
    els.variantSelect.value = selectedVariant ? selectedVariant.id : "";
  }

  function renderAnimationPanel() {
    if (!selectedModel || selectedVariant) {
      els.animationPanel.hidden = true;
      els.animationFilter.value = "";
      els.animationSelect.textContent = "";
      selectedAnimation = null;
      setAnimationEmpty(selectedVariant
        ? "Animations are unavailable while a material variant is selected. Choose Default Material in the Model tab to use animation clips."
        : "Select a model with exported animations.");
      updateAnimationButtons();
      return;
    }
    const allAnimations = animationsForModel(selectedModel);
    if (!allAnimations.length) {
      els.animationPanel.hidden = true;
      els.animationFilter.value = "";
      els.animationSelect.textContent = "";
      selectedAnimation = null;
      setAnimationEmpty("No exported animations are available for this model.");
      updateAnimationButtons();
      return;
    }
    els.animationPanel.hidden = false;
    setAnimationEmpty("");
    els.animationFilter.value = animationFilterText;
    els.animationSelect.textContent = "";
    let animations = filteredAnimationsForModel(selectedModel);
    if (selectedAnimation && !animations.some((animation) => animation.id === selectedAnimation.id)) {
      animations = [selectedAnimation, ...animations];
    }
    const defaultOption = document.createElement("option");
    defaultOption.value = "";
    defaultOption.textContent = animationFilterText.trim()
      ? `Static Model (${animations.length}/${allAnimations.length})`
      : "Static Model";
    els.animationSelect.appendChild(defaultOption);
    for (const animation of animations) {
      const option = document.createElement("option");
      option.value = animation.id;
      option.textContent = animation.label || animation.name || animation.id;
      els.animationSelect.appendChild(option);
    }
    if (animationFilterText.trim() && animations.length === 0) {
      const option = document.createElement("option");
      option.value = "";
      option.disabled = true;
      option.textContent = "No matching animations";
      els.animationSelect.appendChild(option);
    }
    els.animationSelect.value = selectedAnimation ? selectedAnimation.id : "";
    updateAnimationButtons();
  }

  function selectModel(model, options = {}) {
    selectedModel = model;
    selectedVariant = variantById(model, options.variantId) || null;
    selectedAnimation = selectedVariant ? null : (animationById(model, options.animationId) || null);
    if (selectedButton) selectedButton.classList.remove("is-active");
    selectedButton = null;
    selectedMaterialIndex = 0;
    revokeUploadedMaterialTexture();
    if (!els.downloadDialog.hidden) closeDownloadDialog();
    renderVariantPanel();
    renderAnimationPanel();
    renderMaterialControls("Material controls load after the model appears.");
    setAnimationCaptureButtons(true);
    els.selectedTitle.textContent = selectedVariant
      ? `${model.displayName} - ${selectedVariant.label}`
      : selectedAnimation
        ? `${model.displayName} - ${selectedAnimation.label || selectedAnimation.name}`
        : model.displayName;
    els.selectedPath.textContent = selectedVariant
      ? `${model.path} | ${selectedVariant.meshDataPath || "equipment variant"}`
      : selectedAnimation
        ? `${model.path} | ${selectedAnimation.packagePath || "animation"}`
        : model.path;
    const rawUrl = modelRawUrl(model, selectedVariant, selectedAnimation);
    viewerIsLoading = true;
    els.viewer.setAttribute("src", rawUrl);
    els.viewer.src = rawUrl;
    els.viewer.alt = selectedVariant
      ? `${model.displayName} ${selectedVariant.label}`
      : selectedAnimation
        ? `${model.displayName} ${selectedAnimation.label || selectedAnimation.name}`
        : model.displayName;
    els.viewer.autoRotate = false;
    if (selectedAnimation) {
      els.viewer.setAttribute("autoplay", "");
      els.viewer.animationName = selectedAnimation.animationName || selectedAnimation.name || null;
      els.viewer.timeScale = viewerPrefs.animationSpeed;
    } else {
      els.viewer.removeAttribute("autoplay");
      els.viewer.animationName = null;
    }
    els.loadProgress.style.width = "0";
    els.autoRotate.disabled = false;
    els.resetCamera.disabled = false;
    setScreenshotButtons(true, "Screenshot");
    setDownloadButtons(true, "Model is loading...");
    els.fitCamera.disabled = false;
    els.saveCameraView.disabled = false;
    els.loadCameraView.disabled = !readStoredJson(CUSTOM_CAMERA_STORAGE_KEY);
    els.copyLink.disabled = false;
    els.autoRotate.textContent = "Auto Rotate";
    updateAnimationButtons();
    setActionLink(els.openRaw, rawUrl);
    setActionLink(els.openGithub, modelGithubUrl(model, selectedVariant, selectedAnimation));
    if (selectedVariant && selectedVariant.missingTextureCount) {
      els.warning.hidden = false;
      els.warning.textContent = `${selectedVariant.missingTextureCount} variant texture reference${selectedVariant.missingTextureCount === 1 ? "" : "s"} could not be converted.`;
    } else if (model.missingTextureCount) {
      els.warning.hidden = false;
      const sample = (model.missingTextures || []).slice(0, 2).join(", ");
      els.warning.textContent = `${model.missingTextureCount} texture reference${model.missingTextureCount === 1 ? "" : "s"} could not be converted, usually tiny HDR helper/curve-atlas files. ${sample}`;
    } else {
      els.warning.hidden = true;
      els.warning.textContent = "";
    }
    if (options.updateHash) {
      window.history.replaceState(null, "", `#${modelHash(model, selectedVariant, selectedAnimation)}`);
    }
    els.inspectorSummary.textContent = selectedVariant
      ? `${model.displayName} - ${selectedVariant.label}`
      : selectedAnimation
        ? `${model.displayName} - ${selectedAnimation.label || selectedAnimation.name}`
        : model.displayName;
    updateLandingDensity();
    renderResults();
  }

  function selectInitialModel() {
    const parsed = parseHash();
    if (parsed.modelId) {
      const match = models.find((model) => model.id === parsed.modelId);
      if (match) {
        selectModel(match, { updateHash: false, variantId: parsed.variantId, animationId: parsed.animationId });
        els.search.value = match.displayName;
        updateLandingDensity();
        renderResults();
        return;
      }
    }
    renderMaterialControls("Select a model to edit materials.");
    updateAnimationButtons();
    setScreenshotButtons(true, "Screenshot");
    setDownloadButtons(true, "Select a model to download.");
    els.fitCamera.disabled = true;
    els.saveCameraView.disabled = true;
    els.loadCameraView.disabled = !readStoredJson(CUSTOM_CAMERA_STORAGE_KEY);
    syncArStatus();
    updateLandingDensity();
    renderResults();
  }

  async function loadJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`${url} returned ${response.status}`);
    return response.json();
  }

  async function initData() {
    try {
      config = { ...DEFAULT_CONFIG, ...(await loadJson(CONFIG_URL)) };
    } catch {
      config = { ...DEFAULT_CONFIG };
    }
    els.statVersion.textContent = config.datasetVersion;
    const index = await loadJson(INDEX_URL);
    try {
      const variantIndex = await loadJson(VARIANT_INDEX_URL);
      variantsByModel = variantIndex.byModel || {};
    } catch {
      variantsByModel = {};
    }
    try {
      const animationIndex = await loadJson(ANIMATION_INDEX_URL);
      animationsByModel = animationIndex.byModel || {};
    } catch {
      animationsByModel = {};
    }
    models = (index.models || []).map(enrichModel).map(attachVariantSearch).map(attachAnimationSearch);
    els.homeStatus.textContent = "Model index ready.";
    els.statTotal.textContent = `${models.length.toLocaleString()} models`;
    selectInitialModel();
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

  function startAnimationTimelineTicker() {
    const tick = () => {
      if (selectedAnimation && !els.animationPanel.hidden) {
        updateAnimationTimeline();
      }
      window.requestAnimationFrame(tick);
    };
    window.requestAnimationFrame(tick);
  }

  function bindEvents() {
    setMenu("discord-toggle", "discord-menu");
    setMenu("links-toggle", "links-menu");
    restoreViewerControlsOpen();
    restoreInspectorTab();
    renderViewerPreferenceInputs();
    applyViewerPreferences();
    document.addEventListener("click", () => {
      document.querySelectorAll(".rsdw-menu__panel").forEach((panel) => {
        panel.hidden = true;
      });
      document.querySelectorAll(".rsdw-iconbtn[aria-expanded]").forEach((btn) => {
        btn.setAttribute("aria-expanded", "false");
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        if (!els.downloadDialog.hidden) closeDownloadDialog();
        document.querySelectorAll(".rsdw-menu__panel").forEach((panel) => {
          panel.hidden = true;
        });
      }
    });

    els.search.addEventListener("input", () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        els.landing.classList.toggle("is-compact", Boolean(els.search.value.trim()));
        resetVisibleResults();
        updateLandingDensity();
        renderResults();
      }, SEARCH_DEBOUNCE_MS);
    });

    document.querySelectorAll(".kind-filter").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll(".kind-filter").forEach((btn) => btn.classList.remove("is-active"));
        button.classList.add("is-active");
        activeKind = button.dataset.kind || "all";
        resetVisibleResults();
        renderResults();
      });
    });

    if (els.loadMoreResults) {
      els.loadMoreResults.addEventListener("click", () => {
        visibleResultCount += RESULTS_PAGE_SIZE;
        renderResults();
      });
    }

    els.controlsToggle.addEventListener("click", () => {
      setViewerControlsOpen(els.viewerInspector.hidden);
    });
    for (const tab of els.inspectorTabs) {
      tab.addEventListener("click", () => setInspectorTab(tab.dataset.inspectorTab));
    }
    els.materialSelect.addEventListener("change", () => {
      selectedMaterialIndex = clampNumber(els.materialSelect.value, 0, Math.max(0, viewerMaterials().length - 1), 0);
      syncMaterialInputsFromSelected();
      els.materialStatus.textContent = `Editing ${materialLabel(selectedMaterial(), selectedMaterialIndex)}.`;
    });
    els.materialScope.addEventListener("change", () => {
      els.materialStatus.textContent = els.materialScope.value === "all"
        ? "Changes will apply to every material on this model."
        : "Changes will apply to the selected material.";
    });
    els.materialBaseColor.addEventListener("input", applyMaterialFactors);
    els.materialRoughness.addEventListener("input", applyMaterialFactors);
    els.materialMetallic.addEventListener("input", applyMaterialFactors);
    els.materialBaseTexture.addEventListener("change", () => {
      const file = els.materialBaseTexture.files && els.materialBaseTexture.files[0];
      applyUploadedBaseTexture(file);
    });
    els.resetMaterial.addEventListener("click", resetSelectedMaterial);
    els.resetAllMaterials.addEventListener("click", resetAllMaterials);
    els.lightingEnvironmentUpload.addEventListener("change", () => {
      const file = els.lightingEnvironmentUpload.files && els.lightingEnvironmentUpload.files[0];
      if (!file) return;
      if (uploadedEnvironmentUrl) URL.revokeObjectURL(uploadedEnvironmentUrl);
      uploadedEnvironmentUrl = URL.createObjectURL(file);
      updateViewerPreferences({ lightingPreset: "custom" });
      els.lightingEnvironmentStatus.textContent = `Using local environment: ${file.name}`;
    });
    els.lightingShowSkybox.addEventListener("change", () => {
      updateViewerPreference("showSkybox", els.lightingShowSkybox.checked);
    });
    els.clearUploadedEnvironment.addEventListener("click", revokeUploadedEnvironment);
    els.lightingPreset.addEventListener("change", () => {
      applyLightingPreset(els.lightingPreset.value || DEFAULT_VIEWER_PREFS.lightingPreset);
    });
    els.lightingExposure.addEventListener("input", () => {
      updateCustomLightingPreference("exposure", clampNumber(els.lightingExposure.value, 0.2, 2, DEFAULT_VIEWER_PREFS.exposure));
    });
    els.lightingShadow.addEventListener("input", () => {
      updateCustomLightingPreference("shadowIntensity", clampNumber(els.lightingShadow.value, 0, 2, DEFAULT_VIEWER_PREFS.shadowIntensity));
    });
    els.lightingShadowSoftness.addEventListener("input", () => {
      updateCustomLightingPreference("shadowSoftness", clampNumber(els.lightingShadowSoftness.value, 0, 1, DEFAULT_VIEWER_PREFS.shadowSoftness));
    });
    els.lightingToneMapping.addEventListener("change", () => {
      updateCustomLightingPreference("toneMapping", els.lightingToneMapping.value || DEFAULT_VIEWER_PREFS.toneMapping);
    });
    els.lightingEnvironment.addEventListener("change", () => {
      updateCustomLightingPreference("environment", els.lightingEnvironment.value || DEFAULT_VIEWER_PREFS.environment);
    });
    els.resetLighting.addEventListener("click", resetLightingPreferences);
    els.cameraFov.addEventListener("input", () => {
      updateViewerPreference("fieldOfView", clampNumber(els.cameraFov.value, 15, 70, DEFAULT_FIELD_OF_VIEW));
    });
    els.autoRotateSpeed.addEventListener("input", () => {
      updateViewerPreference("autoRotateSpeed", clampNumber(els.autoRotateSpeed.value, 5, 90, DEFAULT_VIEWER_PREFS.autoRotateSpeed));
    });
    els.stageBackground.addEventListener("change", () => {
      updateViewerPreference("stageBackground", els.stageBackground.value || DEFAULT_VIEWER_PREFS.stageBackground);
    });
    els.stageGrid.addEventListener("change", () => {
      updateViewerPreference("stageGrid", els.stageGrid.checked);
    });
    els.fitCamera.addEventListener("click", fitCameraToModel);
    els.saveCameraView.addEventListener("click", saveCameraView);
    els.loadCameraView.addEventListener("click", loadCameraView);
    document.querySelectorAll("[data-camera-orbit]").forEach((button) => {
      button.addEventListener("click", () => {
        els.viewer.cameraOrbit = button.dataset.cameraOrbit;
        if (typeof els.viewer.jumpCameraToGoal === "function") {
          els.viewer.jumpCameraToGoal();
        }
      });
    });
    els.arPlacement.addEventListener("change", () => {
      updateViewerPreference("arPlacement", els.arPlacement.value || DEFAULT_VIEWER_PREFS.arPlacement);
    });
    els.arScale.addEventListener("change", () => {
      updateViewerPreference("arScale", els.arScale.value || DEFAULT_VIEWER_PREFS.arScale);
    });
    els.activateAr.addEventListener("click", () => {
      if (typeof els.viewer.activateAR === "function") {
        els.viewer.activateAR();
      }
    });
    els.captureFormat.addEventListener("change", () => {
      updateViewerPreference("captureFormat", els.captureFormat.value || DEFAULT_VIEWER_PREFS.captureFormat);
    });
    els.captureQuality.addEventListener("input", () => {
      updateViewerPreference("captureQuality", clampNumber(els.captureQuality.value, 0.5, 1, DEFAULT_VIEWER_PREFS.captureQuality));
    });
    els.captureIdealAspect.addEventListener("change", () => {
      updateViewerPreference("captureIdealAspect", els.captureIdealAspect.checked);
    });
    els.animationCaptureFps.addEventListener("input", () => {
      updateViewerPreference("animationCaptureFps", clampNumber(els.animationCaptureFps.value, 8, 60, DEFAULT_VIEWER_PREFS.animationCaptureFps));
    });
    els.animationCaptureMaxFrames.addEventListener("input", () => {
      updateViewerPreference("animationCaptureMaxFrames", clampNumber(els.animationCaptureMaxFrames.value, 60, 900, DEFAULT_VIEWER_PREFS.animationCaptureMaxFrames));
    });
    els.resetViewerPreferences.addEventListener("click", resetViewerPreferences);

    els.variantSelect.addEventListener("change", () => {
      if (!selectedModel) return;
      selectModel(selectedModel, {
        updateHash: true,
        variantId: els.variantSelect.value || null,
      });
    });
    els.animationFilter.addEventListener("input", () => {
      animationFilterText = els.animationFilter.value || "";
      renderAnimationPanel();
    });
    els.animationSelect.addEventListener("change", () => {
      if (!selectedModel) return;
      selectModel(selectedModel, {
        updateHash: true,
        animationId: els.animationSelect.value || null,
      });
    });
    els.animationPrev.addEventListener("click", () => selectAdjacentAnimation(-1));
    els.animationNext.addEventListener("click", () => selectAdjacentAnimation(1));
    els.animationPlay.addEventListener("click", () => {
      if (!selectedAnimation) return;
      if (els.viewer.paused) {
        playSelectedAnimation();
        els.animationPlay.textContent = "Pause";
      } else {
        els.viewer.pause();
        els.animationPlay.textContent = "Play";
      }
      updateAnimationTimeline();
    });
    els.animationRestart.addEventListener("click", restartSelectedAnimation);
    els.animationStepBack.addEventListener("click", () => stepSelectedAnimation(-0.1));
    els.animationStepForward.addEventListener("click", () => stepSelectedAnimation(0.1));
    els.animationScrub.addEventListener("pointerdown", () => {
      isScrubbingAnimation = true;
    });
    els.animationScrub.addEventListener("pointerup", () => {
      isScrubbingAnimation = false;
      updateAnimationTimeline();
    });
    els.animationScrub.addEventListener("change", () => {
      isScrubbingAnimation = false;
      updateAnimationTimeline();
    });
    els.animationScrub.addEventListener("input", () => {
      if (!selectedAnimation) return;
      const duration = currentAnimationDuration();
      els.viewer.currentTime = clampNumber(els.animationScrub.value, 0, duration, 0);
      updateAnimationTimeline();
    });
    els.animationSpeed.addEventListener("change", () => {
      updateViewerPreference("animationSpeed", clampNumber(els.animationSpeed.value, 0.25, 2, DEFAULT_VIEWER_PREFS.animationSpeed));
    });
    els.animationLoopMode.addEventListener("change", () => {
      updateViewerPreference("animationLoopMode", els.animationLoopMode.value || DEFAULT_VIEWER_PREFS.animationLoopMode);
      if (selectedAnimation && !els.viewer.paused) playSelectedAnimation();
    });
    els.captureAnimation.addEventListener("click", captureCurrentAnimationWebp);
    els.captureAnimationPanel.addEventListener("click", captureCurrentAnimationWebp);
    els.downloadModel.addEventListener("click", openDownloadDialog);
    els.downloadClose.addEventListener("click", closeDownloadDialog);
    els.downloadBackdrop.addEventListener("click", closeDownloadDialog);
    els.downloadGlb.addEventListener("click", downloadCurrentGlb);
    els.downloadStl.addEventListener("click", downloadCurrentStl);

    els.autoRotate.addEventListener("click", () => {
      els.viewer.autoRotate = !els.viewer.autoRotate;
      els.autoRotate.textContent = els.viewer.autoRotate ? "Stop Rotate" : "Auto Rotate";
    });
    els.resetCamera.addEventListener("click", () => {
      if (typeof els.viewer.resetTurntableRotation === "function") {
        els.viewer.resetTurntableRotation();
      }
      if (typeof els.viewer.jumpCameraToGoal === "function") {
        els.viewer.cameraOrbit = "0deg 75deg auto";
        els.viewer.jumpCameraToGoal();
      }
    });
    els.saveScreenshot.addEventListener("click", saveCurrentScreenshot);
    els.saveScreenshotPanel.addEventListener("click", saveCurrentScreenshot);
    els.copyLink.addEventListener("click", async () => {
      if (!selectedModel) return;
      const url = `${window.location.origin}${window.location.pathname}#${modelHash(selectedModel, selectedVariant, selectedAnimation)}`;
      try {
        await navigator.clipboard.writeText(url);
        els.copyLink.textContent = "Copied";
        setTimeout(() => {
          els.copyLink.textContent = "Copy Link";
        }, 1200);
      } catch {
        window.prompt("Copy model link", url);
      }
    });
    els.viewer.addEventListener("progress", (event) => {
      const progress = event.detail && typeof event.detail.totalProgress === "number"
        ? event.detail.totalProgress
        : 0;
      els.loadProgress.style.width = `${Math.round(progress * 100)}%`;
    });
    els.viewer.addEventListener("load", () => {
      viewerIsLoading = false;
      els.loadProgress.style.width = "100%";
      captureMaterialBaselines();
      renderMaterialControls("");
      if (selectedAnimation) {
        els.viewer.animationName = selectedAnimation.animationName || selectedAnimation.name || null;
        playSelectedAnimation();
        els.viewer.timeScale = viewerPrefs.animationSpeed;
        els.animationPlay.disabled = false;
        els.animationPlay.textContent = "Pause";
        setAnimationCaptureButtons(false);
      }
      if (selectedModel) {
        setScreenshotButtons(false, "Screenshot");
        setDownloadButtons(false, "Exports run locally in your browser.");
      }
      updateAnimationButtons();
      syncArStatus();
    });
    els.viewer.addEventListener("play", updateAnimationButtons);
    els.viewer.addEventListener("pause", updateAnimationButtons);
    els.viewer.addEventListener("finished", updateAnimationButtons);
    els.viewer.addEventListener("ar-status", syncArStatus);
    els.viewer.addEventListener("ar-tracking", syncArStatus);
    els.viewer.addEventListener("quick-look-button-tapped", () => updateArStatus("Opening iOS Quick Look."));
    els.viewer.addEventListener("error", () => {
      viewerIsLoading = false;
      setScreenshotButtons(true);
      setAnimationCaptureButtons(true);
      setDownloadButtons(true, "Download unavailable because the model failed to load.");
      renderMaterialControls("Material controls are unavailable because the selected model failed to load.");
      updateAnimationButtons();
      els.warning.hidden = false;
      els.warning.textContent = "The selected model could not be loaded. If this is deployed, confirm the WebAssets folder has been pushed to the configured repository branch.";
    });
    startAnimationTimelineTicker();
  }

  bindEvents();
  initData().catch((error) => {
    els.homeStatus.textContent = "Unable to load model index.";
    setStatus(error.message);
    console.error(error);
  });
})();
