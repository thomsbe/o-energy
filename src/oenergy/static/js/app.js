/* o-energy dashboard scripts: theme handling + Chart.js visualisations. */
(function () {
  "use strict";

  const THEME_KEY = "oenergy-theme";
  const root = document.documentElement;

  // ---------------------------------------------------------------- theme
  function themeColor() {
    return getComputedStyle(root).getPropertyValue("--md-surface").trim() || "#0e1512";
  }

  function updateThemeColor() {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) { meta.setAttribute("content", themeColor()); }
  }

  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
    updateThemeColor();
    redrawCharts();
  }

  function initTheme() {
    const stored = localStorage.getItem(THEME_KEY);
    if (!stored) {
      const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
      applyTheme(prefersLight ? "light" : "dark");
    } else {
      root.setAttribute("data-theme", stored);
      updateThemeColor();
    }
    const button = document.getElementById("theme-toggle");
    if (button) {
      button.addEventListener("click", function () {
        const current = root.getAttribute("data-theme");
        applyTheme(current === "dark" ? "light" : "dark");
      });
    }
  }

  // ------------------------------------------------------- Service Worker
  function clearServiceWorkerCache() {
    // nach dem Abmelden dürfen keine Seiten mit Daten im Cache liegen bleiben
    if (!navigator.serviceWorker || !navigator.serviceWorker.controller) { return; }
    navigator.serviceWorker.controller.postMessage({ type: "clear-page-cache" });
  }

  function appVersion() {
    var meta = document.querySelector('meta[name="app-version"]');
    return meta ? meta.getAttribute("content") : "0";
  }

  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) { return; }
    navigator.serviceWorker
      .getRegistrations()
      .then(function (registrations) {
        // veraltete Registrierungen (z.B. aus /static/) entfernen
        registrations.forEach(function (registration) {
          if (registration.scope !== location.origin + "/") {
            registration.unregister();
          }
        });
      })
      .catch(function () {})
      .then(function () {
        return navigator.serviceWorker.register("/sw.js?v=" + appVersion(), { updateViaCache: "none" });
      })
      .catch(function () {
        /* offline-Fähigkeit ist optional */
      });
  }

  // --------------------------------------------------------------- helpers
  const charts = {};

  function cssVar(name, fallback) {
    const value = getComputedStyle(root).getPropertyValue(name).trim();
    return value || fallback;
  }

  function gridColor() {
    return cssVar("--md-outline-variant", "#3f4946");
  }

  function textColor() {
    return cssVar("--md-on-surface-variant", "#bec9c5");
  }

  function fontFamily() {
    return getComputedStyle(document.body).fontFamily || "system-ui, sans-serif";
  }

  function withAlpha(color, alpha) {
    var value = (color || "").trim();
    if (!value) return "rgba(0,0,0," + alpha + ")";
    if (value.charAt(0) === "#") {
      var hex = value.slice(1);
      if (hex.length === 3) {
        hex = hex.split("").map(function (part) { return part + part; }).join("");
      }
      var number = parseInt(hex, 16);
      var r = (number >> 16) & 255;
      var g = (number >> 8) & 255;
      var b = number & 255;
      return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
    }
    if (value.indexOf("rgb") === 0) {
      return value.replace(/rgba?\(([^)]+)\)/, function (_match, body) {
        var parts = body.split(",").map(function (part) { return part.trim(); });
        return "rgba(" + parts[0] + "," + parts[1] + "," + parts[2] + "," + alpha + ")";
      });
    }
    return value;
  }

  function areaFill(color, alpha) {
    return function (context) {
      var chart = context.chart;
      var area = chart.chartArea;
      if (!area) return withAlpha(color, alpha);
      var gradient = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
      gradient.addColorStop(0, withAlpha(color, alpha));
      gradient.addColorStop(1, withAlpha(color, 0));
      return gradient;
    };
  }

  function mergeOptions(base, extra) {
    var out = Object.assign({}, base);
    if (!extra) return out;
    Object.keys(extra).forEach(function (key) {
      var value = extra[key];
      var current = out[key];
      if (
        value && typeof value === "object" && !Array.isArray(value) &&
        current && typeof current === "object" && !Array.isArray(current)
      ) {
        out[key] = Object.assign({}, current, value);
        Object.keys(value).forEach(function (nestedKey) {
          var nestedValue = value[nestedKey];
          var nestedCurrent = current[nestedKey];
          if (
            nestedValue && typeof nestedValue === "object" && !Array.isArray(nestedValue) &&
            nestedCurrent && typeof nestedCurrent === "object" && !Array.isArray(nestedCurrent)
          ) {
            out[key][nestedKey] = Object.assign({}, nestedCurrent, nestedValue);
          }
        });
      } else {
        out[key] = value;
      }
    });
    return out;
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
    var base = {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 320 },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          labels: {
            color: textColor(),
            usePointStyle: true,
            pointStyle: "circle",
            boxWidth: 8,
            boxHeight: 8,
            padding: 16,
            font: { size: 12, family: fontFamily() },
          },
        },
        tooltip: {
          backgroundColor: cssVar("--md-surface-container-high", "#242b28"),
          borderColor: gridColor(),
          borderWidth: 1,
          cornerRadius: 16,
          padding: 12,
          titleColor: cssVar("--md-on-surface", "#dde4e0"),
          bodyColor: textColor(),
          displayColors: true,
          boxPadding: 6,
          usePointStyle: true,
          titleFont: { size: 12.5, family: fontFamily(), weight: "600" },
          bodyFont: { size: 12.5, family: fontFamily() },
        },
      },
      scales: {
        x: {
          grid: { color: gridColor(), drawTicks: false, lineWidth: 1, borderDash: [3, 6] },
          border: { display: false },
          ticks: { color: textColor(), maxRotation: 0, autoSkipPadding: 24, font: { size: 11, family: fontFamily() } },
        },
        y: {
          grid: { color: gridColor(), drawTicks: false, lineWidth: 1, borderDash: [3, 6] },
          border: { display: false },
          ticks: { color: textColor(), padding: 8, font: { size: 11, family: fontFamily() } },
        },
      },
    };
    return mergeOptions(base, extra || {});
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

    const primary = cssVar("--md-primary", "#6fdbc8");
    const secondary = cssVar("--md-secondary", "#b1ccc5");

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
            borderColor: primary,
            backgroundColor: areaFill(primary, 0.30),
            borderWidth: 2.5,
            pointRadius: 0,
            pointHoverRadius: 5,
            fill: true,
            tension: 0.35,
          },
          {
            label: "EPEX Spot (netto)",
            data: epexValues,
            borderColor: secondary,
            borderWidth: 1.6,
            borderDash: [5, 5],
            pointRadius: 0,
            pointHoverRadius: 5,
            tension: 0.3,
            spanGaps: true,
          },
        ],
      },
      options: baseOptions({
        plugins: {
          tooltip: {
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
          y: {
            ticks: { callback: function (value) { return value + " ct"; } },
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

    const palette = [
      cssVar("--md-primary", "#6fdbc8"),
      cssVar("--md-secondary", "#b1ccc5"),
      cssVar("--md-tertiary", "#f2c94c"),
    ];
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
        borderWidth: meta && meta.is_final ? 2.5 : 1.8,
        borderDash: meta && meta.is_final ? [] : [5, 4],
        pointRadius: 0,
        pointHoverRadius: 5,
        tension: 0.35,
        spanGaps: true,
      };
    });

    upsert("chart-consumption", {
      type: "line",
      data: { labels: times.map(clock), datasets: datasets },
      options: baseOptions({
        plugins: {
          tooltip: {
            callbacks: {
              label: function (item) {
                return item.dataset.label + ": " + (item.parsed.y === null ? "–" : item.parsed.y.toFixed(3) + " kWh");
              },
            },
          },
        },
        scales: {
          x: { ticks: { autoSkipPadding: 18 } },
          y: {
            ticks: { callback: function (value) { return value + " kWh"; } },
          },
        },
      }),
    });
  }

  // ---------------------------------------------------------------- costs
  async function renderCosts() {
    const data = await getJSON("/api/chart/costs");
    if (!data || !data.labels || !data.labels.length) return;

    const finalColors = cssVar("--md-primary", "#6fdbc8");
    const provisionalColors = cssVar("--md-tertiary", "#f2c94c");
    const standingColor = withAlpha(cssVar("--md-secondary", "#b1ccc5"), 0.45);

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
            borderRadius: 8,
            borderSkipped: false,
            maxBarThickness: 26,
            stack: "cost",
          },
          {
            label: "Grundpreis",
            data: data.standing_cost,
            backgroundColor: standingColor,
            borderRadius: 8,
            borderSkipped: false,
            maxBarThickness: 26,
            stack: "cost",
          },
        ],
      },
      options: baseOptions({
        plugins: {
          tooltip: {
            callbacks: {
              label: function (item) {
                return item.dataset.label + ": " + item.parsed.y.toFixed(2) + " €";
              },
            },
          },
        },
        scales: {
          x: { stacked: true, grid: { display: false }, ticks: { autoSkipPadding: 12 } },
          y: {
            stacked: true,
            ticks: { callback: function (value) { return value + " €"; } },
          },
        },
      }),
    });
  }


  // ------------------------------------------------ Verbrauch und Preis
  async function renderConsumptionPrice() {
    const data = await getJSON("/api/chart/consumption-price");
    if (!data || !data.labels || !data.labels.length) return;

    const primary = cssVar("--md-primary", "#6fdbc8");
    const tertiary = cssVar("--md-tertiary", "#f2c94c");
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
            backgroundColor: withAlpha(primary, 0.55),
            borderColor: primary,
            borderWidth: 0,
            borderRadius: 8,
            borderSkipped: false,
            maxBarThickness: 18,
            spanGaps: true,
          },
          {
            label: "Preis (ct/kWh)",
            data: data.price,
            type: "line",
            yAxisID: "y1",
            borderColor: tertiary,
            backgroundColor: areaFill(tertiary, 0.18),
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 5,
            tension: 0.35,
            fill: true,
            spanGaps: true,
          },
        ],
      },
      options: baseOptions({
        interaction: { mode: "index", intersect: false },
        plugins: {
          tooltip: {
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
            ticks: { autoSkipPadding: 30 },
          },
          y: {
            position: "left",
            ticks: { callback: function (value) { return value + " kWh"; } },
          },
          y1: {
            position: "right",
            grid: { drawOnChartArea: false },
            ticks: { color: tertiary, callback: function (value) { return value + " ct"; } },
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

    const finalColor = cssVar("--md-primary", "#6fdbc8");
    const provisionalColor = cssVar("--md-tertiary", "#f2c94c");
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
            borderRadius: 8,
            borderSkipped: false,
            maxBarThickness: 26,
          },
        ],
      },
      options: baseOptions({
        plugins: {
          legend: { display: false },
          tooltip: {
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
          x: { grid: { display: false }, ticks: { autoSkipPadding: 12 } },
          y: {
            ticks: { callback: function (value) { return value + " kWh"; } },
          },
        },
      }),
    });
  }

  // ------------------------------------------------------------------ init
  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    if (window.Chart) {
      Chart.defaults.font.family = fontFamily();
      Chart.defaults.font.size = 12;
      Chart.defaults.color = textColor();
    }
    document.querySelectorAll('a[href="/logout"]').forEach(function (link) {
      link.addEventListener("click", clearServiceWorkerCache);
    });
    registerServiceWorker();
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
