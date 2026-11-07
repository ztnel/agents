// Helpers available to every presentation widget as `dm`. Widgets run in an opaque-origin sandbox;
// they talk to the page only through these postMessage calls.
(() => {
  const target = new URL(location.href).origin;
  const post = (message) => parent.postMessage({ demystify: true, ...message }, target);
  const NS = "http://www.w3.org/2000/svg";
  let custom = {};
  let plots = 0;

  const size = () => post({ type: "height", height: Math.ceil(document.documentElement.getBoundingClientRect().height) });

  // Named controls (name or id) plus dm.state() values travel with the human's comments.
  function report() {
    const state = {};
    for (const el of document.querySelectorAll("input, select, textarea")) {
      const key = el.name || el.id;
      if (!key || (el.type === "radio" && !el.checked)) continue;
      state[key] = el.type === "checkbox" ? el.checked : el.value;
    }
    post({ type: "state", state: { ...state, ...custom } });
  }

  addEventListener("DOMContentLoaded", () => {
    new ResizeObserver(size).observe(document.documentElement);
    size();
    report();
  });
  addEventListener("load", size);
  addEventListener("input", report);
  addEventListener("change", report);
  // Esc hands the keyboard back to the page's Vim keys.
  addEventListener("keydown", (event) => {
    if (event.key === "Escape") { event.preventDefault(); post({ type: "escape" }); }
  });

  function el(name, attrs = {}, text) {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function ticks(lo, hi, count = 5) {
    const raw = (hi - lo) / count;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toPrecision(12));
    return out;
  }

  const fmt = (v) => (Math.abs(v) >= 1e4 || (v && Math.abs(v) < 1e-3) ? v.toExponential(1) : String(+v.toPrecision(4)));

  window.dm = {
    // Merge extra values (beyond named controls) into the state reported with comments.
    state(values) { custom = { ...custom, ...values }; report(); },

    // Draw y = f(x) curves into `into` (element or selector), replacing its contents. Call again to redraw.
    // options: { x: [min, max], y?: [min, max], series: [{ f, color?, label? }], xlabel?, ylabel?,
    //            samples? = 400, width? = 640, height? = 260, marks?: [{ x, label?, color? }] }
    plot(into, options) {
      const host = typeof into === "string" ? document.querySelector(into) : into;
      const { x: [x0, x1], series, samples = 400, width = 640, height = 260 } = options;
      const labelled = series.some((s) => s.label);
      const pad = { l: 48, r: 16, t: options.ylabel || labelled ? 30 : 14, b: options.xlabel ? 40 : 26 };
      const xs = Array.from({ length: samples + 1 }, (_, k) => x0 + ((x1 - x0) * k) / samples);
      const data = series.map((s) => xs.map((x) => { const y = s.f(x); return Number.isFinite(y) ? y : null; }));
      let [y0, y1] = options.y || (() => {
        const all = data.flat().filter((v) => v !== null);
        const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || Math.abs(hi) || 1;
        return [lo - span * 0.08, hi + span * 0.08];
      })();
      if (y0 === y1) { y0 -= 1; y1 += 1; }
      const sx = (x) => pad.l + ((x - x0) / (x1 - x0)) * (width - pad.l - pad.r);
      const sy = (y) => height - pad.b - ((y - y0) / (y1 - y0)) * (height - pad.t - pad.b);
      const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "dm-plot", role: "img" });
      for (const t of ticks(x0, x1)) {
        svg.append(el("line", { x1: sx(t), x2: sx(t), y1: pad.t, y2: height - pad.b, class: "grid" }));
        svg.append(el("text", { x: sx(t), y: height - pad.b + 15, "text-anchor": "middle", class: "tick" }, fmt(t)));
      }
      for (const t of ticks(y0, y1)) {
        svg.append(el("line", { x1: pad.l, x2: width - pad.r, y1: sy(t), y2: sy(t), class: "grid" }));
        svg.append(el("text", { x: pad.l - 6, y: sy(t) + 4, "text-anchor": "end", class: "tick" }, fmt(t)));
      }
      if (y0 < 0 && y1 > 0) svg.append(el("line", { x1: pad.l, x2: width - pad.r, y1: sy(0), y2: sy(0), class: "axis" }));
      svg.append(el("rect", { x: pad.l, y: pad.t, width: width - pad.l - pad.r, height: height - pad.t - pad.b, fill: "none", class: "axis" }));
      const clipId = `dmclip${++plots}`;
      const palette = ["#39c5cf", "#e3b341", "#3fb950", "#a371f7", "#f0883e", "#f85149"];
      series.forEach((s, k) => {
        let d = "", pen = "M";
        data[k].forEach((y, j) => {
          if (y === null) { pen = "M"; return; }
          const cy = Math.max(-1e4, Math.min(1e4, sy(y)));
          d += `${pen}${sx(xs[j]).toFixed(1)},${cy.toFixed(1)}`;
          pen = "L";
        });
        const color = s.color || palette[k % palette.length];
        svg.append(el("path", { d, fill: "none", stroke: color, "stroke-width": 2, "clip-path": `url(#${clipId})` }));
      });
      host.replaceChildren(svg); // in the DOM so legend text can be measured
      // Legend: one row above the plot, right-aligned, so it never covers a curve.
      let lx = width - pad.r;
      series.map((s, k) => [s, s.color || palette[k % palette.length]]).filter(([s]) => s.label).reverse().forEach(([s, color]) => {
        const text = el("text", { x: lx, y: 16, "text-anchor": "end", style: `fill:${color}` }, s.label);
        svg.append(text);
        lx -= (text.getComputedTextLength() || s.label.length * 6.4) + 6; // 0 before first layout
        svg.append(el("line", { x1: lx - 14, x2: lx, y1: 12, y2: 12, stroke: color, "stroke-width": 2 }));
        lx -= 32;
      });
      const clip = el("clipPath", { id: clipId });
      clip.append(el("rect", { x: pad.l, y: pad.t, width: width - pad.l - pad.r, height: height - pad.t - pad.b }));
      svg.prepend(clip);
      for (const m of options.marks || []) {
        const color = m.color || "#f0883e";
        svg.append(el("line", { x1: sx(m.x), x2: sx(m.x), y1: pad.t, y2: height - pad.b, stroke: color, "stroke-dasharray": "4 4" }));
        if (m.label) svg.append(el("text", { x: sx(m.x) + 4, y: pad.t + 12, style: `fill:${color}` }, m.label));
      }
      if (options.xlabel) svg.append(el("text", { x: (pad.l + width - pad.r) / 2, y: height - 6, "text-anchor": "middle", class: "muted" }, options.xlabel));
      if (options.ylabel) svg.append(el("text", { x: 4, y: 16, class: "muted" }, options.ylabel));
      return svg;
    },
  };
})();
