(function () {
  "use strict";

  const CONFIG_URL = "./data.config.json";
  const INDEX_URL = "./model-index.json";
  const VARIANT_INDEX_URL = "./equipment-variants.json";
  const RESULTS_PAGE_SIZE = 120;
  const SEARCH_DEBOUNCE_MS = 80;
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
    selectedTitle: document.getElementById("selected-title"),
    selectedPath: document.getElementById("selected-path"),
    warning: document.getElementById("missing-warning"),
    viewer: document.getElementById("model-viewer"),
    variantPanel: document.getElementById("variant-panel"),
    variantSelect: document.getElementById("model-variant-select"),
    loadProgress: document.getElementById("load-progress"),
    autoRotate: document.getElementById("auto-rotate-toggle"),
    resetCamera: document.getElementById("reset-camera"),
    saveScreenshot: document.getElementById("save-screenshot"),
    openRaw: document.getElementById("open-raw"),
    openGithub: document.getElementById("open-github"),
    copyLink: document.getElementById("copy-link"),
  };

  let config = { ...DEFAULT_CONFIG };
  let models = [];
  let variantsByModel = {};
  let activeKind = "all";
  let selectedModel = null;
  let selectedVariant = null;
  let selectedButton = null;
  let debounceTimer = null;
  let visibleResultCount = RESULTS_PAGE_SIZE;

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

  function modelGltfPath(model, variant = selectedVariant) {
    return (variant && variant.gltfPath) || model.gltfPath;
  }

  function modelRawUrl(model, variant = selectedVariant) {
    return `${webAssetBase()}/${encodePath(modelGltfPath(model, variant))}`;
  }

  function modelGithubUrl(model, variant = selectedVariant) {
    return `${githubRepoBase()}/${encodePath(config.datasetVersion)}/WebAssets/${encodePath(modelGltfPath(model, variant))}`;
  }

  function modelHash(model, variant = selectedVariant) {
    const params = new URLSearchParams();
    params.set("model", model.id);
    if (variant && variant.id) params.set("variant", variant.id);
    return params.toString();
  }

  function parseHash() {
    const raw = window.location.hash.replace(/^#/, "");
    const params = new URLSearchParams(raw);
    return {
      modelId: params.get("model"),
      variantId: params.get("variant"),
    };
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

  function screenshotFileName(model) {
    const variantSuffix = selectedVariant && selectedVariant.label ? `_${selectedVariant.label}` : "";
    const baseName = `${model.displayName || model.name || "RSDWModel"}${variantSuffix}`
      .replace(/\.[^.]+$/, "")
      .replace(/[^a-z0-9_-]+/gi, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 96);
    return `${baseName || "RSDWModel"}_${model.kind || "Model"}.png`;
  }

  async function saveCurrentScreenshot() {
    if (!selectedModel || els.saveScreenshot.disabled) return;
    if (typeof els.viewer.toDataURL !== "function") {
      els.warning.hidden = false;
      els.warning.textContent = "Screenshot capture is not available in this browser.";
      return;
    }

    const previousText = els.saveScreenshot.textContent;
    els.saveScreenshot.disabled = true;
    els.saveScreenshot.textContent = "Saving";
    try {
      const dataUrl = await Promise.resolve(els.viewer.toDataURL("image/png"));
      const link = document.createElement("a");
      link.href = dataUrl;
      link.download = screenshotFileName(selectedModel);
      document.body.appendChild(link);
      link.click();
      link.remove();
      els.saveScreenshot.textContent = "Saved";
      window.setTimeout(() => {
        if (selectedModel) {
          els.saveScreenshot.textContent = previousText;
          els.saveScreenshot.disabled = !els.viewer.loaded;
        }
      }, 1200);
    } catch (error) {
      els.warning.hidden = false;
      els.warning.textContent = "Screenshot capture failed. Try again after the model finishes loading.";
      els.saveScreenshot.textContent = previousText;
      els.saveScreenshot.disabled = !els.viewer.loaded;
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

  function selectModel(model, options = {}) {
    selectedModel = model;
    selectedVariant = variantById(model, options.variantId) || null;
    if (selectedButton) selectedButton.classList.remove("is-active");
    selectedButton = null;
    renderVariantPanel();
    els.selectedTitle.textContent = selectedVariant ? `${model.displayName} - ${selectedVariant.label}` : model.displayName;
    els.selectedPath.textContent = selectedVariant ? `${model.path} | ${selectedVariant.meshDataPath || "equipment variant"}` : model.path;
    const rawUrl = modelRawUrl(model, selectedVariant);
    els.viewer.setAttribute("src", rawUrl);
    els.viewer.src = rawUrl;
    els.viewer.alt = selectedVariant ? `${model.displayName} ${selectedVariant.label}` : model.displayName;
    els.viewer.autoRotate = false;
    els.loadProgress.style.width = "0";
    els.autoRotate.disabled = false;
    els.resetCamera.disabled = false;
    els.saveScreenshot.disabled = true;
    els.saveScreenshot.textContent = "Screenshot";
    els.copyLink.disabled = false;
    els.autoRotate.textContent = "Auto Rotate";
    setActionLink(els.openRaw, rawUrl);
    setActionLink(els.openGithub, modelGithubUrl(model, selectedVariant));
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
      window.history.replaceState(null, "", `#${modelHash(model, selectedVariant)}`);
    }
    updateLandingDensity();
    renderResults();
  }

  function selectInitialModel() {
    const parsed = parseHash();
    if (parsed.modelId) {
      const match = models.find((model) => model.id === parsed.modelId);
      if (match) {
        selectModel(match, { updateHash: false, variantId: parsed.variantId });
        els.search.value = match.displayName;
        updateLandingDensity();
        renderResults();
        return;
      }
    }
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
    models = (index.models || []).map(enrichModel).map(attachVariantSearch);
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
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
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

    els.variantSelect.addEventListener("change", () => {
      if (!selectedModel) return;
      selectModel(selectedModel, {
        updateHash: true,
        variantId: els.variantSelect.value || null,
      });
    });

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
    els.copyLink.addEventListener("click", async () => {
      if (!selectedModel) return;
      const url = `${window.location.origin}${window.location.pathname}#${modelHash(selectedModel, selectedVariant)}`;
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
      els.loadProgress.style.width = "100%";
      if (selectedModel) {
        els.saveScreenshot.disabled = false;
        els.saveScreenshot.textContent = "Screenshot";
      }
    });
    els.viewer.addEventListener("error", () => {
      els.saveScreenshot.disabled = true;
      els.warning.hidden = false;
      els.warning.textContent = "The selected model could not be loaded. If this is deployed, confirm the WebAssets folder has been pushed to the configured repository branch.";
    });
  }

  bindEvents();
  initData().catch((error) => {
    els.homeStatus.textContent = "Unable to load model index.";
    setStatus(error.message);
    console.error(error);
  });
})();
