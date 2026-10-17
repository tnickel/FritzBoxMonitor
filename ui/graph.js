// Solar Monitor - Live Graph Script

let chartInstance = null;
const pillCurrent = document.getElementById("pillCurrent");
const pillDaily = document.getElementById("pillDaily");
const pillPeak = document.getElementById("pillPeak");
const btnCloseGraph = document.getElementById("btnCloseGraph");

btnCloseGraph.addEventListener("click", () => {
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.close_graph_window();
  }
});

window.addEventListener("pywebviewready", async () => {
  initChart();
  await updateGraph();
  setInterval(updateGraph, 15000);
});

function initChart() {
  const ctx = document.getElementById("solarChart").getContext("2d");

  // Create subtle gradient for total solar
  const gradientTotal = ctx.createLinearGradient(0, 0, 0, 240);
  gradientTotal.addColorStop(0, "rgba(245, 158, 11, 0.35)");
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
          borderWidth: 2.5,
          tension: 0.35,
          fill: true,
          pointRadius: 1,
          pointHoverRadius: 5,
        },
        {
          label: "Dose 1 (W)",
          data: [],
          borderColor: "#fbbf24",
          backgroundColor: "transparent",
          borderWidth: 1.5,
          tension: 0.35,
          borderDash: [3, 2],
          pointRadius: 0,
          pointHoverRadius: 4,
        },
        {
          label: "Dose 2 (W)",
          data: [],
          borderColor: "#38bdf8",
          backgroundColor: "transparent",
          borderWidth: 1.5,
          tension: 0.35,
          borderDash: [3, 2],
          pointRadius: 0,
          pointHoverRadius: 4,
        },
        {
          label: "Dose 3 (W)",
          data: [],
          borderColor: "#34d399",
          backgroundColor: "transparent",
          borderWidth: 1.5,
          tension: 0.35,
          borderDash: [3, 2],
          pointRadius: 0,
          pointHoverRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 350 },
      plugins: {
        legend: {
          position: "top",
          align: "end",
          labels: {
            boxWidth: 10,
            boxHeight: 10,
            usePointStyle: true,
            color: "#94a3b8",
            font: { size: 10, family: "Segoe UI, sans-serif" },
            padding: 8,
          },
        },
        tooltip: {
          mode: "index",
          intersect: false,
          backgroundColor: "rgba(15, 23, 42, 0.95)",
          titleColor: "#f8fafc",
          bodyColor: "#cbd5e1",
          borderColor: "rgba(255, 255, 255, 0.1)",
          borderWidth: 1,
          padding: 8,
          titleFont: { size: 11, weight: "bold" },
          bodyFont: { size: 11 },
          callbacks: {
            label: function(ctx) {
              return ` ${ctx.dataset.label}: ${Math.round(ctx.parsed.y)} W`;
            }
          }
        },
      },
      scales: {
        x: {
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: {
            color: "#64748b",
            font: { size: 9 },
            maxTicksLimit: 8,
          },
        },
        y: {
          beginAtZero: true,
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: {
            color: "#64748b",
            font: { size: 9 },
            callback: function(val) {
              return `${val} W`;
            },
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

    // Update Pills
    pillCurrent.textContent = `${Math.round(data.current_total_w || 0)} W`;
    const kwhFormatted = (data.today_kwh || 0).toLocaleString("de-DE", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    pillDaily.textContent = `${kwhFormatted} kWh`;

    // Compute Peak
    const history = data.history || [];
    let peak = 0;
    history.forEach((h) => {
      if ((h.total_w || 0) > peak) peak = h.total_w;
    });
    pillPeak.textContent = `${Math.round(peak)} W`;

    // Update labels and datasets
    const labels = history.map((h) => h.time || "");
    const totals = history.map((h) => h.total_w || 0);
    const s1 = history.map((h) => h.s1_w || 0);
    const s2 = history.map((h) => h.s2_w || 0);
    const s3 = history.map((h) => h.s3_w || 0);

    // Update socket names in labels if available
    const names = data.socket_names || [];
    if (names.length > 0) chartInstance.data.datasets[1].label = `${names[0]} (W)`;
    if (names.length > 1) chartInstance.data.datasets[2].label = `${names[1]} (W)`;
    if (names.length > 2) chartInstance.data.datasets[3].label = `${names[2]} (W)`;

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
