// Fritz!Box Solar Monitor - Widget Client Logic

const statusDot = document.getElementById("statusDot");
const widgetHeader = document.getElementById("widgetHeader");
const valSocket1 = document.getElementById("valSocket1");
const valSocket2 = document.getElementById("valSocket2");
const valSocket3 = document.getElementById("valSocket3");
const nameSocket1 = document.getElementById("nameSocket1");
const nameSocket2 = document.getElementById("nameSocket2");
const nameSocket3 = document.getElementById("nameSocket3");

const valTotalCurrent = document.getElementById("valTotalCurrent");
const valDailyKwh = document.getElementById("valDailyKwh");
const valYearKwh = document.getElementById("valYearKwh");
const lblLifetime = document.getElementById("lblLifetime");
const lblUpdated = document.getElementById("lblUpdated");

const btnToggleGraph = document.getElementById("btnToggleGraph");
const btnSettings = document.getElementById("btnSettings");
const btnClose = document.getElementById("btnClose");
const btnOpenGraphDaily = document.getElementById("btnOpenGraphDaily");

// Graph Section Elements
const graphSection = document.getElementById("graphSection");
const lblPeak = document.getElementById("lblPeak");
const tabLive = document.getElementById("tabLive");
const tabMonths = document.getElementById("tabMonths");
let chartInstance = null;
let isGraphExpanded = false;
let currentView = "live"; // "live" | "months"

const SOCKET_COLORS = ["#fbbf24", "#38bdf8", "#34d399"];

// Settings Elements
const settingsModal = document.getElementById("settingsModal");
const btnCloseSettings = document.getElementById("btnCloseSettings");
const cfgHost = document.getElementById("cfgHost");
const cfgPassword = document.getElementById("cfgPassword");
const cfgInterval = document.getElementById("cfgInterval");
const cfgAutostart = document.getElementById("cfgAutostart");
const btnDiscoverDevices = document.getElementById("btnDiscoverDevices");
const deviceSelectionList = document.getElementById("deviceSelectionList");
const btnSaveSettings = document.getElementById("btnSaveSettings");
const settingsStatusMsg = document.getElementById("settingsStatusMsg");

let availableDevices = [];
let selectedAins = [];

// Startup markers from the JS side -> widget.log (diagnosis of startup hangs)
function uiLog(msg) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.log_event) {
    try { window.pywebview.api.log_event(msg); } catch (e) { /* bridge not ready */ }
  }
}
window.addEventListener("error", (e) => {
  uiLog(`JS-Fehler: ${e.message} (${(e.filename || "").split("/").pop()}:${e.lineno})`);
});

// Block WebView2 page zoom (ctrl+mouse-wheel / precision-touchpad pinch).
// Any zoom != 100% shrinks the CSS viewport and cuts off the fixed layout.
window.addEventListener("wheel", (e) => {
  if (e.ctrlKey) e.preventDefault();
}, { passive: false });
window.addEventListener("keydown", (e) => {
  if (e.ctrlKey && ["+", "-", "=", "0"].includes(e.key)) e.preventDefault();
});

// Verify that the CSS viewport matches the requested logical size and
// self-correct the native window size. This works regardless of how the
// DPI scaling of the system/window is configured.
async function syncSize(width, height) {
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.nudge_size) return;
  for (let i = 0; i < 6; i++) {
    if (Math.abs(window.innerWidth - width) <= 2 && Math.abs(window.innerHeight - height) <= 2) return;
    const fx = window.innerWidth > 0 ? width / window.innerWidth : 1;
    const fy = window.innerHeight > 0 ? height / window.innerHeight : 1;
    try {
      await window.pywebview.api.nudge_size(fx, fy);
    } catch (e) {
      return;
    }
    await new Promise((r) => setTimeout(r, 90));
  }
}

// Native Window Dragging on Header Mousedown (Butter-smooth, zero jump)
widgetHeader.addEventListener("mousedown", (e) => {
  if (e.button === 0 && !e.target.closest(".actions")) {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.start_drag) {
      window.pywebview.api.start_drag();
    }
  }
});

// Initialize when pywebview is ready
window.addEventListener("pywebviewready", async () => {
  uiLog("pywebviewready gefeuert");
  // Pin the WebView2 rasterization scale to the real window DPI first,
  // then bring the window size in line with the CSS design.
  try {
    if (window.pywebview.api.apply_display_fix) await window.pywebview.api.apply_display_fix();
    uiLog("apply_display_fix aufgerufen");
  } catch (e) { uiLog(`apply_display_fix Fehler: ${e}`); }
  await syncSize(370, 260);
  uiLog("syncSize(370, 260) abgeschlossen");
  await loadWidgetData();
  uiLog("erste Daten geladen");
  await initSettings();
  uiLog("Einstellungen initialisiert");

  // Set up periodic update
  setInterval(loadWidgetData, 15000); // UI poll sync
});

// Update display with latest readings
async function loadWidgetData() {
  if (!window.pywebview || !window.pywebview.api) return;

  try {
    const data = await window.pywebview.api.get_latest_data();
    if (!data) return;

    if (data.status === "online") {
      statusDot.className = "status-dot online";
      statusDot.title = "Fritz!Box verbunden";
    } else {
      statusDot.className = "status-dot offline";
      statusDot.title = data.error || "Fritz!Box nicht erreichbar";
    }

    // Update 3 Sockets
    const sockets = data.sockets || [];
    if (sockets.length > 0) {
      nameSocket1.textContent = sockets[0].name || "Dose 1";
      valSocket1.textContent = Math.round(sockets[0].power_w);
    }
    if (sockets.length > 1) {
      nameSocket2.textContent = sockets[1].name || "Dose 2";
      valSocket2.textContent = Math.round(sockets[1].power_w);
    }
    if (sockets.length > 2) {
      nameSocket3.textContent = sockets[2].name || "Dose 3";
      valSocket3.textContent = Math.round(sockets[2].power_w);
    }

    // Update Totals
    valTotalCurrent.textContent = `${Math.round(data.total_power_w || 0)} W`;
    
    // Format Tagessumme with comma: e.g. 0,05
    const kwhFormatted = (data.total_today_kwh || 0).toLocaleString("de-DE", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    valDailyKwh.textContent = kwhFormatted;

    // Format Jahressumme: e.g. 2.595
    const yearFormatted = Math.round(data.total_year_kwh || 0).toLocaleString("de-DE");
    valYearKwh.textContent = yearFormatted;

    // Lifetime
    if (data.total_lifetime_kwh) {
      lblLifetime.textContent = `Gesamt: ${Math.round(data.total_lifetime_kwh).toLocaleString("de-DE")} kWh`;
    }

    lblUpdated.textContent = data.timestamp ? `Stand: ${data.timestamp.substring(0, 5)}` : "Stand: --:--";

    if (isGraphExpanded) {
      await updateGraph();
    }

  } catch (err) {
    statusDot.className = "status-dot offline";
    console.error("Error loading widget data:", err);
  }
}

// Toggle Graph Expansion
async function toggleGraph() {
  isGraphExpanded = !isGraphExpanded;

  if (isGraphExpanded) {
    graphSection.classList.add("active");
    if (window.pywebview && window.pywebview.api) {
      await window.pywebview.api.resize_widget(370, 620);
      await syncSize(370, 620);
    }
    initOrUpdateChart();
    await updateGraph();
    setTimeout(() => {
      if (chartInstance) chartInstance.resize();
    }, 100);
    setTimeout(() => {
      if (chartInstance) chartInstance.resize();
    }, 300);
  } else {
    graphSection.classList.remove("active");
    if (window.pywebview && window.pywebview.api) {
      await window.pywebview.api.resize_widget(370, 260);
      await syncSize(370, 260);
    }
  }
}

window.addEventListener("resize", () => {
  if (chartInstance && isGraphExpanded) {
    chartInstance.resize();
  }
});

btnToggleGraph.addEventListener("click", toggleGraph);
btnOpenGraphDaily.addEventListener("click", toggleGraph);

// View switch: live line chart vs monthly bar chart
tabLive.addEventListener("click", () => setGraphView("live"));
tabMonths.addEventListener("click", () => setGraphView("months"));

function setGraphView(view) {
  if (currentView === view) return;
  currentView = view;
  tabLive.classList.toggle("active", view === "live");
  tabMonths.classList.toggle("active", view === "months");
  if (chartInstance) {
    chartInstance.destroy();
    chartInstance = null;
  }
  initOrUpdateChart();
  updateGraph();
}

function initOrUpdateChart() {
  if (chartInstance) {
    chartInstance.resize();
    return;
  }
  if (currentView === "months") {
    createMonthsChart();
  } else {
    createLiveChart();
  }
}

function createMonthsChart() {
  const canvas = document.getElementById("solarChart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  chartInstance = new Chart(ctx, {
    type: "bar",
    data: {
      labels: [],
      datasets: [0, 1, 2].map((i) => ({
        label: "",
        data: [],
        backgroundColor: SOCKET_COLORS[i],
        borderRadius: 2,
        maxBarThickness: 18,
      })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      layout: {
        padding: { bottom: 12, left: 2, right: 6, top: 4 },
      },
      plugins: {
        legend: {
          display: true,
          position: "top",
          labels: {
            boxWidth: 8,
            boxHeight: 8,
            usePointStyle: true,
            color: "#94a3b8",
            font: { size: 9 },
            padding: 6,
          },
        },
        tooltip: {
          padding: 6,
          titleFont: { size: 10 },
          bodyFont: { size: 10 },
          callbacks: {
            label: (ctx) => ` ${ctx.dataset.label}: ${(ctx.parsed.y).toLocaleString("de-DE")} kWh`,
            footer: (items) => {
              const sum = items.reduce((acc, it) => acc + it.parsed.y, 0);
              return `Gesamt: ${sum.toLocaleString("de-DE", { maximumFractionDigits: 1 })} kWh`;
            },
          },
          footerFont: { size: 10, weight: "bold" },
        },
      },
      scales: {
        x: {
          stacked: true,
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: {
            color: "#94a3b8",
            font: { size: 8 },
            maxRotation: 0,
          },
        },
        y: {
          stacked: true,
          beginAtZero: true,
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: {
            color: "#94a3b8",
            font: { size: 8 },
            callback: (v) => `${v} kWh`,
          },
        },
      },
    },
  });
}

function updateMonthsChart(monthly) {
  if (!chartInstance || currentView !== "months") return;
  const labels = monthly.labels || [];
  const sockets = monthly.sockets || [];
  lblPeak.textContent = "";

  sockets.forEach((s, i) => {
    if (i >= chartInstance.data.datasets.length) return;
    chartInstance.data.datasets[i].label = s.name || "";
    chartInstance.data.datasets[i].data = s.values_kwh || [];
  });
  chartInstance.data.labels = labels;
  chartInstance.update();

  // Peak pill shows the strongest single month (sum over sockets)
  let max = 0;
  for (let i = 0; i < labels.length; i++) {
    const sum = sockets.reduce((acc, s) => acc + ((s.values_kwh || [])[i] || 0), 0);
    if (sum > max) max = sum;
  }
  lblPeak.textContent = `Stärkster Monat: ${max.toLocaleString("de-DE", { maximumFractionDigits: 1 })} kWh`;
}

function createLiveChart() {
  const canvas = document.getElementById("solarChart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const gradientTotal = ctx.createLinearGradient(0, 0, 0, 240);
  gradientTotal.addColorStop(0, "rgba(245, 158, 11, 0.4)");
  gradientTotal.addColorStop(1, "rgba(245, 158, 11, 0.0)");

  chartInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Gesamt (W)",
          data: [],
          borderColor: "#f59e0b",
          backgroundColor: gradientTotal,
          borderWidth: 2.2,
          tension: 0.35,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 4,
        },
        {
          label: "Garage Süd",
          data: [],
          borderColor: "#fbbf24",
          borderWidth: 1.5,
          tension: 0.35,
          pointRadius: 0,
        },
        {
          label: "Garage Ost",
          data: [],
          borderColor: "#38bdf8",
          borderWidth: 1.5,
          tension: 0.35,
          pointRadius: 0,
        },
        {
          label: "Schuppen",
          data: [],
          borderColor: "#34d399",
          borderWidth: 1.5,
          tension: 0.35,
          pointRadius: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      layout: {
        padding: {
          bottom: 12,
          left: 2,
          right: 6,
          top: 4,
        },
      },
      plugins: {
        legend: {
          display: true,
          position: "top",
          labels: {
            boxWidth: 8,
            boxHeight: 8,
            usePointStyle: true,
            color: "#94a3b8",
            font: { size: 9 },
            padding: 6,
          },
        },
        tooltip: {
          mode: "index",
          intersect: false,
          padding: 6,
          titleFont: { size: 10 },
          bodyFont: { size: 10 },
          callbacks: {
            label: (ctx) => ` ${ctx.dataset.label}: ${Math.round(ctx.parsed.y)} W`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: {
            color: "#94a3b8",
            font: { size: 8 },
            maxTicksLimit: 6,
            maxRotation: 0,
          },
        },
        y: {
          beginAtZero: true,
          suggestedMin: 0,
          suggestedMax: 400,
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: {
            color: "#94a3b8",
            font: { size: 8 },
            callback: (v) => `${v}W`,
          },
        },
      },
    },
  });
}

async function updateGraph() {
  if (!window.pywebview || !window.pywebview.api || !chartInstance) return;

  try {
    const data = await window.pywebview.api.get_graph_data();
    if (!data) return;

    if (currentView === "months") {
      updateMonthsChart(data.monthly || { labels: [], sockets: [] });
      return;
    }

    const history = data.history || [];
    let peak = 0;
    history.forEach((h) => {
      if ((h.total_w || 0) > peak) peak = h.total_w;
    });
    lblPeak.textContent = `Spitze: ${Math.round(peak)} W`;

    const labels = history.map((h) => h.time || "");
    const totals = history.map((h) => h.total_w || 0);
    const s1 = history.map((h) => h.s1_w || 0);
    const s2 = history.map((h) => h.s2_w || 0);
    const s3 = history.map((h) => h.s3_w || 0);

    const names = data.socket_names || [];
    if (names.length > 0) chartInstance.data.datasets[1].label = names[0];
    if (names.length > 1) chartInstance.data.datasets[2].label = names[1];
    if (names.length > 2) chartInstance.data.datasets[3].label = names[2];

    chartInstance.data.labels = labels;
    chartInstance.data.datasets[0].data = totals;
    chartInstance.data.datasets[1].data = s1;
    chartInstance.data.datasets[2].data = s2;
    chartInstance.data.datasets[3].data = s3;

    chartInstance.update();
  } catch (err) {
    console.error("Error refreshing graph data:", err);
  }
}

// Close App
btnClose.addEventListener("click", () => {
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.close_app();
  }
});

// Settings Modal Handlers
btnSettings.addEventListener("click", async () => {
  settingsModal.classList.add("active");
  await initSettings();
});

btnCloseSettings.addEventListener("click", () => {
  settingsModal.classList.remove("active");
});

async function initSettings() {
  if (!window.pywebview || !window.pywebview.api) return;
  try {
    const cfg = await window.pywebview.api.get_config();
    if (cfg) {
      cfgHost.value = cfg.host || "192.168.178.1";
      cfgPassword.value = cfg.password || "";
      cfgInterval.value = String(cfg.interval_seconds || 60);
      selectedAins = cfg.socket_ains || [];
    }
    if (window.pywebview.api.get_autostart) {
      cfgAutostart.checked = await window.pywebview.api.get_autostart();
    }
  } catch (e) {
    console.error("Failed to load config:", e);
  }
}

// Autostart checkbox applies immediately (independent of Speichern)
cfgAutostart.addEventListener("change", async () => {
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.set_autostart) return;
  try {
    const res = await window.pywebview.api.set_autostart(cfgAutostart.checked);
    if (!res.success) throw new Error(res.error || "Unbekannter Fehler");
    settingsStatusMsg.textContent = res.enabled
      ? "Autostart aktiviert - das Widget startet künftig mit dem PC."
      : "Autostart deaktiviert.";
    settingsStatusMsg.className = "status-msg success";
  } catch (e) {
    cfgAutostart.checked = !cfgAutostart.checked; // revert on failure
    settingsStatusMsg.textContent = `Autostart konnte nicht geändert werden: ${e}`;
    settingsStatusMsg.className = "status-msg error";
  }
});

// Discover Devices
btnDiscoverDevices.addEventListener("click", async () => {
  settingsStatusMsg.textContent = "Suche Steckdosen an der Fritz!Box...";
  settingsStatusMsg.className = "status-msg";

  const host = cfgHost.value.trim();
  const password = cfgPassword.value;

  if (!password) {
    settingsStatusMsg.textContent = "Bitte zuerst das Fritz!Box-Kennwort eingeben.";
    settingsStatusMsg.className = "status-msg error";
    return;
  }

  try {
    const res = await window.pywebview.api.discover_devices(host, password);
    if (!res.success) {
      settingsStatusMsg.textContent = `Fehler: ${res.error}`;
      settingsStatusMsg.className = "status-msg error";
      return;
    }

    availableDevices = res.devices || [];
    if (availableDevices.length === 0) {
      deviceSelectionList.innerHTML = '<span class="hint">Keine Steckdosen mit Strommessung gefunden.</span>';
      settingsStatusMsg.textContent = "Keine passenden Steckdosen gefunden.";
      settingsStatusMsg.className = "status-msg error";
      return;
    }

    renderDeviceList();
    settingsStatusMsg.textContent = `${availableDevices.length} Steckdose(n) gefunden. Wähle die 3 Solar-Dosen:`;
    settingsStatusMsg.className = "status-msg success";

  } catch (err) {
    settingsStatusMsg.textContent = `Verbindungsfehler: ${err}`;
    settingsStatusMsg.className = "status-msg error";
  }
});

function renderDeviceList() {
  deviceSelectionList.innerHTML = "";
  availableDevices.forEach((dev) => {
    const isChecked = selectedAins.includes(dev.ain) || (selectedAins.length === 0 && deviceSelectionList.children.length < 3);
    const item = document.createElement("label");
    item.className = "device-item";
    item.innerHTML = `
      <input type="checkbox" value="${dev.ain}" ${isChecked ? "checked" : ""}>
      <span>${dev.name} (${dev.productname || "DECT"}) - ${Math.round(dev.power_w)} W</span>
    `;
    item.querySelector("input").addEventListener("change", (e) => {
      const checkedBoxes = deviceSelectionList.querySelectorAll("input:checked");
      if (checkedBoxes.length > 3) {
        e.target.checked = false;
        alert("Es können maximal 3 Solar-Steckdosen ausgewählt werden.");
      }
    });
    deviceSelectionList.appendChild(item);
  });
}

// Save Settings
btnSaveSettings.addEventListener("click", async () => {
  const host = cfgHost.value.trim() || "192.168.178.1";
  const password = cfgPassword.value;
  const interval = parseInt(cfgInterval.value, 10) || 60;

  const checkedInputs = deviceSelectionList.querySelectorAll("input:checked");
  const ains = Array.from(checkedInputs).map(cb => cb.value);

  settingsStatusMsg.textContent = "Speichere und teste Verbindung...";
  settingsStatusMsg.className = "status-msg";

  try {
    const res = await window.pywebview.api.save_config({
      host: host,
      password: password,
      interval_seconds: interval,
      socket_ains: ains.length > 0 ? ains : selectedAins,
    });

    if (res.success) {
      settingsStatusMsg.textContent = "Erfolgreich verbunden!";
      settingsStatusMsg.className = "status-msg success";
      setTimeout(() => {
        settingsModal.classList.remove("active");
        loadWidgetData();
      }, 700);
    } else {
      settingsStatusMsg.textContent = `Fehler: ${res.error}`;
      settingsStatusMsg.className = "status-msg error";
    }
  } catch (e) {
    settingsStatusMsg.textContent = `Fehler: ${e}`;
    settingsStatusMsg.className = "status-msg error";
  }
});
