
/*! m2bin-hotfix.js — keep charts compact and align M2bin to labels */
(function () {
  function onReady(fn) {
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
      setTimeout(fn, 0);
    } else {
      document.addEventListener('DOMContentLoaded', fn, { once: true });
    }
  }

  function injectCSS() {
    const css = `
      #charts canvas { height: 320px !important; }
      .chart-card canvas { height: 320px !important; }
    `;
    const style = document.createElement('style');
    style.setAttribute('data-m2bin-hotfix', 'true');
    style.textContent = css;
    document.head.appendChild(style);
  }

  function fixChartAxis(chart) {
    if (!chart || !chart.options) return;
    chart.options.maintainAspectRatio = false;
    if (!chart.options.scales) chart.options.scales = {};
    // Ensure binary axis exists and stays 0..1 on the right without expanding layout
    chart.options.scales.bin = {
      type: 'linear',
      position: 'right',
      min: -0.1,
      max: 1.1,
      offset: false,
      grid: { drawOnChartArea: false },
      ticks: {
        stepSize: 1,
        callback: (v) => (v === 0 || v === 1 ? v : ''),
      },
    };
  }

  function alignBinData(chart) {
    const lbls = chart?.data?.labels;
    if (!Array.isArray(lbls)) return;
    const ds = chart.data.datasets || [];
    const bin = ds.find(d => (d.label || '').toLowerCase().includes('m2bin') || d.yAxisID === 'bin');
    if (!bin) return;

    // If data are numbers, wrap them into {x, y} with matching labels
    if (Array.isArray(bin.data) && bin.data.length && typeof bin.data[0] !== 'object') {
      bin.parsing = false;
      bin.data = lbls.map((x, i) => ({ x, y: Number.isFinite(bin.data[i]) ? bin.data[i] : null }));
    }

    // Force to binary 0/1 and clip if any stray values crept in
    if (Array.isArray(bin.data)) {
      bin.data = bin.data.map(p => {
        if (!p) return { x: null, y: null };
        const x = (typeof p === 'object') ? p.x : null;
        const yv = (typeof p === 'object') ? p.y : p;
        let y = (yv >= 1 ? 1 : (yv <= 0 ? 0 : yv));
        // If value was something else (e.g., threshold like 50), normalize to 0/1
        if (y > 1 || y < 0) y = y >= 0.5 ? 1 : 0;
        return { x, y };
      });
      bin.yAxisID = 'bin';
      bin.borderColor = bin.borderColor || '#111';
      bin.backgroundColor = bin.backgroundColor || '#111';
      bin.borderDash = bin.borderDash || [6, 4];
      bin.tension = 0;
      bin.pointRadius = 0;
      bin.spanGaps = true;
    }
  }

  function relayout(chart) {
    try { chart.update('none'); } catch (e) {}
  }

  onReady(function () {
    injectCSS();

    // Chart.js 4 keeps a Map in Chart.instances
    const charts = [];
    try {
      if (window.Chart) {
        if (Chart.instances && typeof Chart.instances.forEach === 'function') {
          Chart.instances.forEach(c => charts.push(c));
        } else if (Array.isArray(Chart.instances)) {
          charts.push(...Chart.instances);
        }
      }
    } catch (e) {}

    charts.forEach(c => {
      fixChartAxis(c);
      alignBinData(c);
      relayout(c);
    });

    // Also observe future charts in case the page lazy-loads
    const obs = new MutationObserver(() => {
      const fresh = [];
      try {
        if (window.Chart) {
          if (Chart.instances && typeof Chart.instances.forEach === 'function') {
            Chart.instances.forEach(c => fresh.push(c));
          } else if (Array.isArray(Chart.instances)) {
            fresh.push(...Chart.instances);
          }
        }
      } catch (e) {}
      fresh.forEach(c => { fixChartAxis(c); alignBinData(c); relayout(c); });
    });
    obs.observe(document.body, { childList: true, subtree: true });
  });
})();
