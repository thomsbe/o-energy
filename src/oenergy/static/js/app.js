/* o-energy dashboard scripts: theme handling + Chart.js visualisations. */
(function () {
  "use strict";

  const THEME_KEY = "oenergy-theme";
  const root = document.documentElement;

  // ---------------------------------------------------------------- theme
  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
    redrawCharts();
  }

  function initTheme() {
    const stored = localStorage.getItem(THEME_KEY);
    if (!stored) {
      const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
      applyTheme(prefersLight ? "light" : "dark");
    } else {
      root.setAttribute("data-theme", stored);
    }
    const button = document.getElementById("theme-toggle");
    if (button) {
      button.addEventListener("click", function () {
        const current = root.getAttribute("data-theme");
        applyTheme(current === "dark" ? "light" : "dark");
      });
    }
  }

  // --------------------------------------------------------------- helpers
  const charts = {};

  function gridColor() {
    return getComputedStyle(root).getPropertyValue("--border-soft").trim() || "#232b36";
  }

  function textColor() {
    return getComputedStyle(root).getPropertyValue("--muted").trim() || "#8b949e";
  }

  function cssVar(name, fallback) {
    const value = getComputedStyle(root).getPropertyValue(name).trim();
    return value || fallback;
  }

  function clock(iso) {
    const date = new Date(iso);
    return String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0");
  }

  function dayLabel(iso) {
    const date = new Date(iso);
    return (
      String(date.getDate()).padStart(2, "0") +
      "." +
      String(date.getMonth() + 1).padStart(2, "0")
    );
  }

  function fullLabel(iso) {
    const date = new Date(iso);
    return date.toLocaleString("de-DE", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  async function getJSON(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (!response.ok) return null;
    return response.json();
  }

  function baseOptions(extra) {
    return Object.assign(
      {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            labels: { color: textColor(), boxWidth: 12, boxHeight: 12, usePointStyle: true },
          },
          tooltip: {
            backgroundColor: "#10151c",
            borderColor: gridColor(),
            borderWidth: 1,
            titleColor: "#e6edf3",
            bodyColor: "#c9d1d9",
            padding: 10,
          },
        },
        scales: {
          x: {
            grid: { color: gridColor(), drawTicks: false },
            ticks: { color: textColor(), maxRotation: 0, autoSkipPadding: 24 },
          },
          y: {
            grid: { color: gridColor(), drawTicks: false },
            ticks: { color: textColor(), padding: 6 },
          },
        },
      },
      extra || {}
    );
  }

  function upsert(canvasId, config) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    if (charts[canvasId]) {
      charts[canvasId].data = config.data;
      charts[canvasId].options = config.options;
      charts[canvasId].update("none");
      return;
    }
    charts[canvasId] = new Chart(canvas.getContext("2d"), config);
  }

  function redrawCharts() {
    Object.keys(charts).forEach(function (id) {
      charts[id].destroy();
      delete charts[id];
    });
    renderPrices();
    renderConsumption();
    renderConsumptionPrice();
    renderMonthly();
    renderCosts();
  }

  // ---------------------------------------------------------------- prices
  async function renderPrices() {
    const data = await getJSON("/api/chart/prices");
    if (!data || !data.customer || !data.customer.length) return;

    const labels = data.customer.map(function (entry) {
      return clock(entry.start);
    });
    const epexMap = {};
    (data.epex || []).forEach(function (entry) {
      epexMap[entry.start] = entry.net;
    });
    const epexValues = data.customer.map(function (entry) {
      const value = epexMap[entry.start];
      return value === undefined || value === null ? null : value;
    });

    upsert("chart-prices", {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Kundenpreis (brutto)",
            data: data.customer.map(function (entry) {
              return entry.gross !== null && entry.gross !== undefined ? entry.gross : entry.net;
            }),
            borderColor: cssVar("--accent", "#3fb950"),
            backgroundColor: "rgba(63,185,80,.14)",
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            fill: true,
            tension: 0.25,
          },
          {
            label: "EPEX Spot (netto)",
            data: epexValues,
            borderColor: cssVar("--info", "#58a6ff"),
            borderWidth: 1.5,
            borderDash: [4, 4],
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.25,
            spanGaps: true,
          },
        ],
      },
      options: baseOptions({
        plugins: {
          legend: { labels: { color: textColor(), usePointStyle: true } },
          tooltip: {
            backgroundColor: "#10151c",
            borderColor: gridColor(),
            borderWidth: 1,
            callbacks: {
              title: function (items) {
                const entry = data.customer[items[0].dataIndex];
                return entry ? fullLabel(entry.start) : "";
              },
              label: function (item) {
                return item.dataset.label + ": " + (item.parsed.y === null ? "–" : item.parsed.y.toFixed(2) + " ct/kWh");
              },
            },
          },
        },
        scales: {
          x: { grid: { color: gridColor(), drawTicks: false }, ticks: { color: textColor(), maxRotation: 0, autoSkipPadding: 24 } },
          y: {
            grid: { color: gridColor(), drawTicks: false },
            ticks: { color: textColor(), callback: function (value) { return value + " ct"; } },
          },
        },
      }),
    });
  }

  // ----------------------------------------------------------- consumption
  async function renderConsumption() {
    const data = await getJSON("/api/chart/consumption");
    if (!data || !data.series) return;

    const dayKeys = Object.keys(data.series).sort();
    if (!dayKeys.length) return;

    const times = [];
    dayKeys.forEach(function (day) {
      data.series[day].forEach(function (point) {
        if (times.indexOf(point.t) === -1) times.push(point.t);
      });
    });
    times.sort();

    const palette = [cssVar("--info", "#58a6ff"), cssVar("--accent", "#3fb950"), cssVar("--warn", "#d29922")];
    const datasets = dayKeys.map(function (day, index) {
      const lookup = {};
      data.series[day].forEach(function (point) {
        lookup[point.t] = point.kwh;
      });
      const meta = (data.days || []).find(function (item) {
        return item.day === day;
      });
      return {
        label: dayLabel(day) + " " + (meta && meta.is_final ? "" : "(provisorisch)"),
        data: times.map(function (time) {
          return lookup[time] === undefined ? null : lookup[time];
        }),
        borderColor: palette[index % palette.length],
        backgroundColor: "transparent",
        borderWidth: meta && meta.is_final ? 2 : 1.5,
        borderDash: meta && meta.is_final ? [] : [5, 4],
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.3,
        spanGaps: true,
      };
    });

    upsert("chart-consumption", {
      type: "line",
      data: { labels: times.map(clock), datasets: datasets },
      options: baseOptions({
        plugins: {
          legend: { labels: { color: textColor(), usePointStyle: true } },
          tooltip: {
            backgroundColor: "#10151c",
            borderColor: gridColor(),
            borderWidth: 1,
            callbacks: {
              label: function (item) {
                return item.dataset.label + ": " + (item.parsed.y === null ? "–" : item.parsed.y.toFixed(3) + " kWh");
              },
            },
          },
        },
        scales: {
          x: { grid: { color: gridColor(), drawTicks: false }, ticks: { color: textColor(), maxRotation: 0, autoSkipPadding: 18 } },
          y: {
            grid: { color: gridColor(), drawTicks: false },
            ticks: { color: textColor(), callback: function (value) { return value + " kWh"; } },
          },
        },
      }),
    });
  }

  // ---------------------------------------------------------------- costs
  async function renderCosts() {
    const data = await getJSON("/api/chart/costs");
    if (!data || !data.labels || !data.labels.length) return;

    const finalColors = cssVar("--info", "#58a6ff");
    const provisionalColors = cssVar("--warn", "#d29922");

    upsert("chart-costs", {
      type: "bar",
      data: {
        labels: data.labels.map(dayLabel),
        datasets: [
          {
            label: "Energiekosten",
            data: data.energy_cost,
            backgroundColor: data.is_final.map(function (isFinal) {
              return isFinal ? finalColors : provisionalColors;
            }),
            borderRadius: 3,
            stack: "cost",
          },
          {
            label: "Grundpreis",
            data: data.standing_cost,
            backgroundColor: "rgba(139,148,158,.45)",
            borderRadius: 3,
            stack: "cost",
          },
        ],
      },
      options: baseOptions({
        plugins: {
          legend: { labels: { color: textColor(), usePointStyle: true } },
          tooltip: {
            backgroundColor: "#10151c",
            borderColor: gridColor(),
            borderWidth: 1,
            callbacks: {
              label: function (item) {
                return item.dataset.label + ": " + item.parsed.y.toFixed(2) + " €";
              },
            },
          },
        },
        scales: {
          x: { stacked: true, grid: { display: false }, ticks: { color: textColor(), maxRotation: 0, autoSkipPadding: 12 } },
          y: {
            stacked: true,
            grid: { color: gridColor(), drawTicks: false },
            ticks: { color: textColor(), callback: function (value) { return value + " €"; } },
          },
        },
      }),
    });
  }


  // ------------------------------------------------ Verbrauch und Preis
  async function renderConsumptionPrice() {
    const data = await getJSON("/api/chart/consumption-price");
    if (!data || !data.labels || !data.labels.length) return;

    const labels = data.labels.map(clock);
    upsert("chart-consumption-price", {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Verbrauch (kWh)",
            data: data.kwh,
            type: "bar",
            yAxisID: "y",
            backgroundColor: cssVar("--info", "#58a6ff") + "66",
            borderColor: cssVar("--info", "#58a6ff"),
            borderWidth: 0,
            borderRadius: 2,
            spanGaps: true,
          },
          {
            label: "Preis (ct/kWh)",
            data: data.price,
            type: "line",
            yAxisID: "y1",
            borderColor: cssVar("--warn", "#d29922"),
            backgroundColor: cssVar("--warn", "#d29922") + "22",
            borderWidth: 1.8,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.3,
            fill: true,
            spanGaps: true,
          },
        ],
      },
      options: baseOptions({
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: textColor(), usePointStyle: true } },
          tooltip: {
            backgroundColor: "#10151c",
            borderColor: gridColor(),
            borderWidth: 1,
            callbacks: {
              title: function (items) {
                return fullLabel(data.labels[items[0].dataIndex]);
              },
              label: function (item) {
                if (item.parsed.y === null) return item.dataset.label + ": –";
                return (
                  item.dataset.label +
                  ": " +
                  (item.datasetIndex === 0 ? item.parsed.y.toFixed(3) : item.parsed.y.toFixed(2))
                );
              },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { color: textColor(), maxRotation: 0, autoSkipPadding: 30 },
          },
          y: {
            position: "left",
            grid: { color: gridColor(), drawTicks: false },
            ticks: { color: textColor(), callback: function (value) { return value + " kWh"; } },
          },
          y1: {
            position: "right",
            grid: { drawOnChartArea: false },
            ticks: { color: cssVar("--warn", "#d29922"), callback: function (value) { return value + " ct"; } },
          },
        },
      }),
    });
  }

  // ------------------------------------------------------ Monatsverbrauch
  async function renderMonthly(month) {
    const canvas = document.getElementById("chart-monthly");
    const selected = month || (canvas && canvas.dataset.month) || "";
    const query = selected ? "?month=" + encodeURIComponent(selected) : "";
    const data = await getJSON("/api/chart/monthly" + query);
    if (!data || !data.days || !data.days.length) return;

    const finalColor = cssVar("--info", "#58a6ff");
    const provisionalColor = cssVar("--warn", "#d29922");
    upsert("chart-monthly", {
      type: "bar",
      data: {
        labels: data.days.map(function (day) { return day.label; }),
        datasets: [
          {
            label: "Verbrauch (kWh)",
            data: data.days.map(function (day) { return day.kwh; }),
            backgroundColor: data.days.map(function (day) {
              return day.is_final ? finalColor : provisionalColor;
            }),
            borderRadius: 3,
          },
        ],
      },
      options: baseOptions({
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#10151c",
            borderColor: gridColor(),
            borderWidth: 1,
            callbacks: {
              title: function (items) {
                const day = data.days[items[0].dataIndex];
                return day.day + (day.is_final ? " (endgültig)" : " (provisorisch)");
              },
              label: function (item) {
                const day = data.days[item.dataIndex];
                if (item.parsed.y === null) return "keine Zählerdaten";
                return (
                  item.parsed.y.toFixed(3) + " kWh · " + (day.cost_cents / 100).toFixed(2) + " €"
                );
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: textColor(), maxRotation: 0, autoSkipPadding: 12 } },
          y: {
            grid: { color: gridColor(), drawTicks: false },
            ticks: { color: textColor(), callback: function (value) { return value + " kWh"; } },
          },
        },
      }),
    });
  }

  // ------------------------------------------------------------------ init
  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    renderPrices();
    renderConsumption();
    renderConsumptionPrice();
    renderMonthly();
    renderCosts();

    // keep the charts fresh
    setInterval(function () {
      renderPrices();
      renderConsumption();
      renderConsumptionPrice();
      renderMonthly();
      renderCosts();
    }, 60000);

    document.body.addEventListener("htmx:afterSwap", function (event) {
      if (event.target && event.target.id === "tiles") {
        renderPrices();
      }
    });
  });
})();
