// The scene: the tape the engine recorded, played as light on wires.
//
// Every frame is a function of the time `t` alone. The tape's events are
// laid on a timeline at a fixed tempo, and `draw(t)` sets the whole scene
// from it, so `seek(t)` shows any moment and a recorder can step through
// the run frame by frame. Every number on the page is the tape's: the
// page rolls from one recorded value to the next and computes none.
(function () {
  "use strict";

  const TAPE = window.TAPE;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.getElementById("scene");

  // ---------------------------------------------------------------- the look

  const C = {
    night: "#090e1c",
    ink: "#c9d3ee",
    dim: "#6b789e",
    faint: "#2a3658",
    wire: "#2a3b6e",
    cloud: "#16214099",
    cloudLine: "#2b3b6b",
    hoop: "#d9b874",
    web: "#8ea2d8",
    rotor: "#eef3ff",
    person: "#ffd27a",
    request: "#5fd4ff",
    response: "#a48cff",
    tool: "#ffe066",
    result: "#7ee08a",
    report: "#e2a8ff",
    task: "#ff9ed8",
    refused: "#ff5c5c",
    parked: "#ff6bcb",
    good: "#7ee08a",
    bad: "#ff6b6b",
  };

  const TRAVEL = 0.14; // seconds a light takes along a wire
  const BURST = 0.04; // between the lights of a streamed response
  const TYPING = 0.1; // the hands at work before a tool's result leaves
  const TICK = 0.06; // a step that sends no light
  const TURN = 0.42; // a quarter of the rotor's settle, eased like water
  const INTRO = 0.7;
  const CLOSE = 2.2;
  const HANDS = new Set(["read_file", "run_check", "edit_file"]);
  const CELL = { w: 15, h: 25 }; // one wheel of a meter

  const P = {
    person: { x: 116, y: 330 },
    engine: { x: 500, y: 316, r: 90 },
    subs: [null, { x: 736, y: 218, r: 34 }, { x: 736, y: 414, r: 34 }],
    brain: { x: 1088, y: 150 },
    keys: { x: 1094, y: 532 },
    screen: { x: 930, y: 292, w: 326, h: 196 },
  };

  // ---------------------------------------------------------------- helpers

  function el(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    (parent || svg).appendChild(node);
    return node;
  }

  function text(parent, x, y, content, attrs) {
    const node = el("text", Object.assign({ x, y }, attrs || {}), parent);
    node.textContent = content;
    return node;
  }

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, f) => a + (b - a) * f;
  const smooth = (f) => f * f * (3 - 2 * f);

  // Water: a fluid ease, a slight overshoot, a settle.
  function water(f) {
    if (f <= 0) return 0;
    if (f >= 1) return 1;
    return 1 - Math.exp(-5.2 * f) * Math.cos(2 * Math.PI * 0.82 * f);
  }

  function wrap(line, width) {
    const words = String(line).split(" ");
    const lines = [];
    let current = "";
    for (const word of words) {
      if (current && (current + " " + word).length > width) {
        lines.push(current);
        current = word;
      } else {
        current = current ? current + " " + word : word;
      }
    }
    if (current || !lines.length) lines.push(current);
    return lines;
  }

  function hard(line, width) {
    const out = [];
    let rest = String(line);
    while (rest.length > width) {
      out.push(rest.slice(0, width));
      rest = "  " + rest.slice(width);
    }
    out.push(rest);
    return out;
  }

  function money(micros, places) {
    return "$" + (micros / 1e6).toFixed(places);
  }

  // ---------------------------------------------------------------- the stage

  const defs = el("defs");
  const glow = el("filter", { id: "glow", x: "-200%", y: "-200%", width: "500%", height: "500%" }, defs);
  el("feGaussianBlur", { stdDeviation: "3.2", result: "b" }, glow);
  const merge = el("feMerge", {}, glow);
  el("feMergeNode", { in: "b" }, merge);
  el("feMergeNode", { in: "SourceGraphic" }, merge);
  const soft = el("filter", { id: "soft", x: "-50%", y: "-50%", width: "200%", height: "200%" }, defs);
  el("feGaussianBlur", { stdDeviation: "6" }, soft);
  const vignette = el("radialGradient", { id: "vignette", cx: "50%", cy: "45%", r: "75%" }, defs);
  el("stop", { offset: "0%", "stop-color": "#111a33" }, vignette);
  el("stop", { offset: "100%", "stop-color": C.night }, vignette);

  el("rect", { x: 0, y: 0, width: 1280, height: 720, fill: "url(#vignette)" });

  // A cloud: arcs bulging out between points on an ellipse.
  function cloud(cx, cy, rx, ry, bumps, seed) {
    const pts = [];
    for (let i = 0; i < bumps; i++) {
      const a = (i / bumps) * Math.PI * 2 + seed;
      const wob = 1 + 0.05 * Math.sin(i * 2.7 + seed * 3);
      pts.push([cx + Math.cos(a) * rx * wob, cy + Math.sin(a) * ry * wob]);
    }
    let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
    for (let i = 1; i <= bumps; i++) {
      const [x0, y0] = pts[i - 1];
      const [x1, y1] = pts[i % bumps];
      const r = Math.hypot(x1 - x0, y1 - y0) * (0.56 + 0.08 * Math.sin(i * 1.9 + seed));
      d += ` A${r.toFixed(1)},${r.toFixed(1)} 0 0 1 ${x1.toFixed(1)},${y1.toFixed(1)}`;
    }
    return d + " Z";
  }

  const ourCloud = el("g");
  el("path", { d: cloud(560, 330, 275, 218, 14, 0.2), fill: C.cloud, stroke: C.cloudLine, "stroke-width": 1.5 }, ourCloud);
  text(ourCloud, 318, 150, "our cloud", { fill: C.dim, "font-size": 13, "letter-spacing": 1.5 });

  const modelCloud = el("g");
  el("path", { d: cloud(1088, 150, 158, 100, 11, 1.1), fill: C.cloud, stroke: C.cloudLine, "stroke-width": 1.5 }, modelCloud);
  text(modelCloud, 1088, 70, "the model's cloud", { fill: C.dim, "font-size": 13, "letter-spacing": 1.5, "text-anchor": "middle" });

  // Header and the act's label.
  text(svg, 28, 40, "dreamcatcher", { fill: "#eef2ff", "font-size": 22, "font-weight": 700 });
  text(svg, 28, 60, "the engine's run, played from its record · the model scripted · no network", {
    fill: C.dim,
    "font-size": 12.5,
  });
  const actTitle = text(svg, 28, 96, "", { fill: C.request, "font-size": 15, "font-weight": 600 });
  const actAbout = text(svg, 28, 114, "", { fill: C.dim, "font-size": 12 });

  // ---------------------------------------------------------------- wires

  const wireLayer = el("g");
  const W = {};
  function wire(name, d, width) {
    const path = el("path", { d, fill: "none", stroke: C.wire, "stroke-width": width, "stroke-linecap": "round" }, wireLayer);
    W[name] = { path, length: path.getTotalLength(), width };
  }
  const E = P.engine;
  const at = (c, deg, r) => [c.x + Math.cos((deg * Math.PI) / 180) * r, c.y + Math.sin((deg * Math.PI) / 180) * r];
  {
    const [ex1, ey1] = at(E, -38, E.r);
    const [ex2, ey2] = at(E, 36, E.r);
    const [ex3, ey3] = at(E, -16, E.r);
    const [ex4, ey4] = at(E, 14, E.r);
    const s1 = P.subs[1];
    const s2 = P.subs[2];
    wire("person", `M${P.person.x + 34},${P.person.y - 8} C 260,${P.person.y - 70} 330,${E.y + 40} ${E.x - E.r},${E.y}`, 2.4);
    wire("brain0", `M${ex1},${ey1} C 620,120 820,70 ${P.brain.x - 58},${P.brain.y - 6}`, 2.4);
    wire("hands0", `M${ex2},${ey2} C 640,520 820,560 ${P.keys.x - 150},${P.keys.y + 2}`, 2.4);
    wire("sub1", `M${ex3},${ey3} C 640,262 660,${s1.y + 10} ${s1.x - s1.r},${s1.y}`, 1.6);
    wire("sub2", `M${ex4},${ey4} C 640,380 660,${s2.y - 10} ${s2.x - s2.r},${s2.y}`, 1.6);
    wire("brain1", `M${s1.x + s1.r},${s1.y - 8} C 860,190 940,170 ${P.brain.x - 58},${P.brain.y + 8}`, 1.3);
    wire("brain2", `M${s2.x + s2.r},${s2.y - 10} C 880,360 960,230 ${P.brain.x - 50},${P.brain.y + 24}`, 1.3);
    wire("hands1", `M${s1.x + 10},${s1.y + s1.r} C 800,330 860,470 ${P.keys.x - 150},${P.keys.y - 10}`, 1.3);
    wire("hands2", `M${s2.x + s2.r},${s2.y + 8} C 840,460 880,520 ${P.keys.x - 150},${P.keys.y + 12}`, 1.3);
  }
  for (const k of [1, 2]) for (const w of ["sub", "brain", "hands"]) W[w + k].path.setAttribute("opacity", 0);

  // ---------------------------------------------------------------- the person

  const person = el("g");
  const personHalo = el("circle", { cx: P.person.x, cy: P.person.y - 6, r: 52, fill: C.person, opacity: 0, filter: "url(#soft)" }, person);
  el("circle", { cx: P.person.x, cy: P.person.y - 32, r: 17, fill: "#1b2547", stroke: C.person, "stroke-width": 2.2 }, person);
  el(
    "path",
    {
      d: `M${P.person.x - 32},${P.person.y + 26} C ${P.person.x - 32},${P.person.y - 8} ${P.person.x + 32},${P.person.y - 8} ${P.person.x + 32},${P.person.y + 26} Z`,
      fill: "#1b2547",
      stroke: C.person,
      "stroke-width": 2.2,
    },
    person,
  );
  text(person, P.person.x, P.person.y + 50, "the person", { fill: C.ink, "font-size": 13, "text-anchor": "middle" });
  const bubble = el("g", { opacity: 0 });
  const bubbleBox = el("rect", { x: 26, y: 156, width: 236, height: 70, rx: 10, fill: "#141d3a", stroke: C.person, "stroke-width": 1.2 }, bubble);
  el("path", { d: `M${P.person.x - 8},226 L${P.person.x},244 L${P.person.x + 8},226 Z`, fill: "#141d3a" }, bubble);
  const bubbleLines = [0, 1, 2, 3].map((n) => text(bubble, 40, 178 + n * 16, "", { fill: "#f4ead2", "font-size": 12.5 }));

  // ---------------------------------------------------------------- the engine

  function webPath(r, spokes, rounds) {
    let d = "";
    const total = spokes * rounds;
    for (let k = 0; k <= total; k++) {
      const turn = k / spokes;
      const a = (k * 2 * Math.PI) / spokes + turn * (Math.PI / spokes) - Math.PI / 2;
      const rr = r * (1 - (0.8 * k) / total);
      d += (k ? " L" : "M") + (Math.cos(a) * rr).toFixed(1) + "," + (Math.sin(a) * rr).toFixed(1);
    }
    return d;
  }

  function rotorPath(r) {
    // Three swept blades, their tips the corners of a triangle.
    let d = "";
    for (let i = 0; i < 3; i++) {
      const a = (i * 2 * Math.PI) / 3 - Math.PI / 2;
      const tip = [Math.cos(a) * r, Math.sin(a) * r];
      const left = [Math.cos(a - 0.62) * r * 0.2, Math.sin(a - 0.62) * r * 0.2];
      const right = [Math.cos(a + 0.62) * r * 0.2, Math.sin(a + 0.62) * r * 0.2];
      const bow = [Math.cos(a + 0.34) * r * 0.62, Math.sin(a + 0.34) * r * 0.62];
      d += `M${left[0].toFixed(1)},${left[1].toFixed(1)} Q${bow[0].toFixed(1)},${bow[1].toFixed(1)} ${tip[0].toFixed(1)},${tip[1].toFixed(1)} L${right[0].toFixed(1)},${right[1].toFixed(1)} Z `;
    }
    return d;
  }

  function trianglePath(r) {
    const pts = [0, 1, 2].map((i) => {
      const a = (i * 2 * Math.PI) / 3 - Math.PI / 2;
      return `${(Math.cos(a) * r).toFixed(1)},${(Math.sin(a) * r).toFixed(1)}`;
    });
    return `M${pts[0]} L${pts[1]} L${pts[2]} Z`;
  }

  function feather(len) {
    return `M0,0 C ${-len * 0.22},${len * 0.3} ${-len * 0.16},${len * 0.75} 0,${len} C ${len * 0.16},${len * 0.75} ${len * 0.22},${len * 0.3} 0,0 Z`;
  }

  function makeEngine(c, big) {
    const g = el("g", { transform: `translate(${c.x},${c.y})` });
    const halo = el("circle", { r: c.r * 1.15, fill: C.request, opacity: 0, filter: "url(#soft)" }, g);
    const feathers = [];
    if (big) {
      for (const [deg, len] of [
        [118, 62],
        [90, 78],
        [62, 62],
      ]) {
        const [fx, fy] = [Math.cos((deg * Math.PI) / 180) * c.r, Math.sin((deg * Math.PI) / 180) * c.r];
        const holder = el("g", { transform: `translate(${fx.toFixed(1)},${fy.toFixed(1)})` }, g);
        const swing = el("g", {}, holder);
        el("line", { x1: 0, y1: 0, x2: 0, y2: 16, stroke: C.hoop, "stroke-width": 1.2 }, swing);
        el("circle", { cx: 0, cy: 16, r: 3.4, fill: C.request }, swing);
        const plume = el("g", { transform: "translate(0,20)" }, swing);
        el("path", { d: feather(len - 22), fill: "#dfe6fb", opacity: 0.85 }, plume);
        el("line", { x1: 0, y1: 2, x2: 0, y2: len - 24, stroke: "#8ea2d8", "stroke-width": 1 }, plume);
        feathers.push(swing);
      }
    }
    el("circle", { r: c.r, fill: "#0e1630", stroke: C.hoop, "stroke-width": big ? 5 : 2.6 }, g);
    el("circle", { r: c.r - (big ? 6 : 3), fill: "none", stroke: C.hoop, "stroke-width": 0.8, opacity: 0.5 }, g);
    const web = el("g", {}, g);
    el("path", { d: webPath(c.r - (big ? 4 : 2), big ? 9 : 7, big ? 5 : 3), fill: "none", stroke: C.web, "stroke-width": big ? 1.1 : 0.8, opacity: 0.7 }, web);
    if (big) {
      for (let k = 0; k < 9; k++) {
        const a = (k * 2 * Math.PI) / 9 + Math.PI / 9 - Math.PI / 2;
        const rr = c.r * 0.55;
        el("circle", { cx: (Math.cos(a) * rr).toFixed(1), cy: (Math.sin(a) * rr).toFixed(1), r: 2.4, fill: k % 3 ? C.web : C.hoop }, web);
      }
    }
    const ripples = [0, 1, 2].map(() => el("circle", { r: 10, fill: "none", stroke: C.request, "stroke-width": 1.6, opacity: 0 }, g));
    const rotor = el("g", {}, g);
    const rr = c.r * (big ? 0.52 : 0.56);
    el("path", { d: trianglePath(rr), fill: "none", stroke: C.rotor, "stroke-width": big ? 1.8 : 1, opacity: 0.5 }, rotor);
    el("path", { d: rotorPath(rr), fill: C.rotor, opacity: 0.95 }, rotor);
    el("circle", { cx: 0, cy: -rr, r: big ? 4.5 : 2.4, fill: C.hoop }, rotor);
    el("circle", { r: big ? 9 : 4, fill: C.night, stroke: C.rotor, "stroke-width": big ? 2.6 : 1.4 }, rotor);
    const pulse = el("circle", { r: big ? 14 : 7, fill: C.rotor, opacity: 0, filter: "url(#glow)" }, g);
    return { g, halo, feathers, web, rotor, ripples, pulse };
  }

  const engines = [makeEngine(E, true), makeEngine(P.subs[1], false), makeEngine(P.subs[2], false)];
  text(svg, E.x, E.y - E.r - 34, "the engine", { fill: C.ink, "font-size": 14, "text-anchor": "middle", "font-weight": 600 });
  const engineState = text(svg, E.x, E.y - E.r - 16, "", { fill: C.dim, "font-size": 12.5, "text-anchor": "middle" });
  const badgeAt = { x: E.x + E.r + 2, y: E.y - E.r - 6 };
  text(svg, badgeAt.x - 6, badgeAt.y + 17, "epoch", { fill: C.dim, "font-size": 11, "text-anchor": "end" });
  const badge = odometer(svg, badgeAt.x, badgeAt.y, 1);
  const engineNote = text(svg, E.x, E.y + E.r + 112, "", { fill: C.refused, "font-size": 13, "text-anchor": "middle", "font-weight": 600 });
  const subLabels = [null, 1, 2].map((k) => {
    if (!k) return null;
    const s = P.subs[k];
    const g = el("g", { opacity: 0 });
    const title = (TAPE.sessions[k] && TAPE.sessions[k].title) || "sub-agent";
    text(g, s.x, s.y + s.r + 17, "sub-agent · " + title, { fill: C.ink, "font-size": 11.5, "text-anchor": "middle" });
    const state = text(g, s.x, s.y + s.r + 31, "", { fill: C.dim, "font-size": 11, "text-anchor": "middle" });
    return { g, state };
  });
  for (const k of [1, 2]) engines[k].g.setAttribute("opacity", 0);

  // ---------------------------------------------------------------- the model

  const B = P.brain;
  const brain = el("g");
  const brainHalo = el("circle", { cx: B.x, cy: B.y, r: 70, fill: C.response, opacity: 0, filter: "url(#soft)" }, brain);
  el("line", { x1: B.x, y1: B.y - 34, x2: B.x, y2: B.y - 52, stroke: "#9fb0e0", "stroke-width": 2 }, brain);
  const bulb = el("circle", { cx: B.x, cy: B.y - 55, r: 5, fill: "#3a4a7a" }, brain);
  el("rect", { x: B.x - 60, y: B.y - 10, width: 10, height: 26, rx: 3, fill: "#25325c" }, brain);
  el("rect", { x: B.x + 50, y: B.y - 10, width: 10, height: 26, rx: 3, fill: "#25325c" }, brain);
  el("rect", { x: B.x - 50, y: B.y - 34, width: 100, height: 70, rx: 18, fill: "#1a2550", stroke: "#9fb0e0", "stroke-width": 2.2 }, brain);
  const eyes = [-20, 20].map((dx) => el("circle", { cx: B.x + dx, cy: B.y - 6, r: 8, fill: "#3a4a7a" }, brain));
  el("rect", { x: B.x - 22, y: B.y + 14, width: 44, height: 9, rx: 4, fill: "#0e1630", stroke: "#9fb0e0", "stroke-width": 1 }, brain);
  for (const dx of [-11, 0, 11]) el("line", { x1: B.x + dx, y1: B.y + 14, x2: B.x + dx, y2: B.y + 23, stroke: "#9fb0e0", "stroke-width": 1 }, brain);
  text(brain, B.x, B.y + 58, "the model", { fill: C.ink, "font-size": 13, "text-anchor": "middle" });
  const said = [0, 1].map((n) => text(brain, B.x, B.y + 78 + n * 15, "", { fill: "#bcaefc", "font-size": 11.5, "text-anchor": "middle", "font-style": "italic" }));

  // ---------------------------------------------------------------- the hands

  const S = P.screen;
  const screen = el("g");
  el("rect", { x: S.x, y: S.y, width: S.w, height: S.h, rx: 10, fill: "#0b1124", stroke: "#33447a", "stroke-width": 1.5 }, screen);
  el("rect", { x: S.x, y: S.y, width: S.w, height: 24, rx: 10, fill: "#18213f" }, screen);
  el("rect", { x: S.x, y: S.y + 14, width: S.w, height: 10, fill: "#18213f" }, screen);
  for (const [n, col] of [
    [0, "#ff6b6b"],
    [1, "#ffd27a"],
    [2, "#7ee08a"],
  ])
    el("circle", { cx: S.x + 14 + n * 13, cy: S.y + 12, r: 4, fill: col, opacity: 0.8 }, screen);
  const screenTitle = text(screen, S.x + 56, S.y + 16.5, "the workspace", { class: "mono", fill: C.dim, "font-size": 11.5 });
  const screenLines = [0, 1, 2, 3, 4, 5, 6, 7].map((n) => text(screen, S.x + 14, S.y + 46 + n * 17.5, "", { class: "mono", "font-size": 12, fill: C.ink }));
  const screenFlash = el("rect", { x: S.x, y: S.y, width: S.w, height: S.h, rx: 10, fill: "none", stroke: C.tool, "stroke-width": 2, opacity: 0 }, screen);

  const K = P.keys;
  const keyboard = el("g");
  el("rect", { x: K.x - 150, y: K.y - 20, width: 300, height: 40, rx: 8, fill: "#141c38", stroke: "#33447a", "stroke-width": 1.4 }, keyboard);
  const keyCaps = [];
  for (let row = 0; row < 3; row++) {
    for (let col = 0; col < 14; col++) {
      keyCaps.push(el("rect", { x: K.x - 142 + col * 20.4 + row * 4, y: K.y - 15 + row * 10.5, width: 16, height: 8, rx: 2, fill: "#24305a" }, keyboard));
    }
  }
  function hand(cx, cy, mirror) {
    const g = el("g", { transform: `translate(${cx},${cy}) scale(${mirror ? -1 : 1},1)` });
    const fingers = [];
    for (const [i, dx, len] of [
      [0, -15, 22],
      [1, -5, 27],
      [2, 5, 28],
      [3, 15, 24],
    ]) {
      const f = el("g", {}, g);
      el("rect", { x: dx - 4.2, y: -len, width: 8.4, height: len + 6, rx: 4.2, fill: "#e3d2c2", stroke: "#8a7466", "stroke-width": 0.8 }, f);
      fingers[i] = f;
    }
    el("rect", { x: -22, y: -4, width: 44, height: 36, rx: 14, fill: "#e3d2c2", stroke: "#8a7466", "stroke-width": 0.8 }, g);
    el("rect", { x: 18, y: 0, width: 9, height: 22, rx: 4.5, fill: "#e3d2c2", stroke: "#8a7466", "stroke-width": 0.8, transform: "rotate(-38 22 6)" }, g);
    return fingers;
  }
  const fingers = [...hand(K.x - 62, K.y + 20, false), ...hand(K.x + 62, K.y + 20, true)];
  text(svg, K.x, K.y + 74, "the hands · outside every cloud", { fill: C.ink, "font-size": 13, "text-anchor": "middle" });

  const legend = el("g");
  [
    ["the person", C.person],
    ["to the model", C.request],
    ["from the model", C.response],
    ["to the hands", C.tool],
    ["from the hands", C.result],
    ["to and from a sub-agent", C.report],
    ["a write refused", C.refused],
  ].forEach(([label, color], j) => {
    el("circle", { cx: 34, cy: 462 + j * 19, r: 4.5, fill: color, filter: "url(#glow)" }, legend);
    text(legend, 46, 466 + j * 19, label, { fill: C.dim, "font-size": 11.5 });
  });

  // ---------------------------------------------------------------- the numbers

  const strip = el("g");
  el("rect", { x: 0, y: 612, width: 1280, height: 108, fill: "#070b17" }, strip);
  el("line", { x1: 0, y1: 612, x2: 1280, y2: 612, stroke: C.faint }, strip);

  const last = TAPE.events[TAPE.events.length - 1].n;

  function odometer(parent, x, y, digits, scale) {
    const cells = [];
    for (let i = 0; i < digits; i++) {
      const cx = x + i * (CELL.w + 1);
      const clip = el("clipPath", { id: `clip-${parent.id || "m"}-${x}-${i}` }, defs);
      el("rect", { x: cx, y, width: CELL.w, height: CELL.h }, clip);
      el("rect", { x: cx, y, width: CELL.w, height: CELL.h, rx: 2, fill: "#111a33" }, parent);
      const holder = el("g", { "clip-path": `url(#clip-${parent.id || "m"}-${x}-${i})` }, parent);
      const reel = el("g", {}, holder);
      for (let d = 0; d <= 10; d++) {
        text(reel, cx + CELL.w / 2, y + 19 + d * CELL.h, String(d % 10), { class: "mono", "font-size": 17, fill: "#f1f4ff", "text-anchor": "middle" });
      }
      cells.push(reel);
    }
    el("rect", { x, y: y + CELL.h / 2 - 0.5, width: digits * (CELL.w + 1), height: 1, fill: C.night, opacity: 0.6 }, parent);
    return { cells, digits, scale: scale || 1 };
  }

  // A gas meter: the last wheel turns with the value; each wheel left of
  // it turns only while the wheel to its right rolls from 9 to 0.
  function roll(meter, value) {
    const v = Math.max(0, value / meter.scale);
    for (let i = 0; i < meter.digits; i++) {
      const k = meter.digits - 1 - i;
      const unit = Math.pow(10, k);
      let pos = Math.floor(v / unit) % 10;
      if (k === 0) pos = v % 10;
      else {
        const low = v % unit;
        pos += Math.max(0, low - (unit - 1));
      }
      meter.cells[i].setAttribute("transform", `translate(0,${(-pos * CELL.h).toFixed(2)})`);
    }
  }

  const METERS = [
    ["steps", "steps"],
    ["loops", "loops"],
    ["model calls", "model_calls"],
    ["tool calls", "tool_calls"],
    ["tokens in", "tokens_in"],
    ["tokens out", "tokens_out"],
    ["cache read", "tokens_cache"],
    ["epoch", "epoch"],
  ];
  const meters = {};
  let capText, holdText, gauge;
  {
    let x = 28;
    for (const [label, key] of METERS) {
      const digits = Math.max(1, String(last[key] || 0).length);
      const width = Math.max(digits * (CELL.w + 1), label.length * 6.9);
      text(strip, x, 640, label.toUpperCase(), { fill: C.dim, "font-size": 10.5, "letter-spacing": 1 });
      meters[key] = odometer(strip, x, 650, digits);
      x += width + 22;
    }
    const sx = x + 6;
    text(strip, sx, 640, "SPEND", { fill: C.dim, "font-size": 10.5, "letter-spacing": 1 });
    text(strip, sx, 669, "$0.", { class: "mono", "font-size": 17, fill: "#f1f4ff" });
    meters.cost_micros = odometer(strip, sx + 32, 650, 4);
    capText = text(strip, sx + 104, 668, "", { class: "mono", "font-size": 12.5, fill: C.dim });
    holdText = text(strip, sx + 230, 668, "", { class: "mono", "font-size": 12.5, fill: C.request });
    gauge = { x: sx, y: 686, w: 1252 - sx, h: 12 };
  }
  const scale = 1.08 * Math.max(...TAPE.events.map((e) => e.n.cap_micros || 0), 1);
  el("rect", { x: gauge.x, y: gauge.y, width: gauge.w, height: gauge.h, rx: 3, fill: "#111a33" }, strip);
  const gaugeOver = el("rect", { x: gauge.x, y: gauge.y, width: 0, height: gauge.h, fill: "#1d1430" }, strip);
  const gaugeSpend = el("rect", { x: gauge.x, y: gauge.y, width: 0, height: gauge.h, rx: 3, fill: "#f1f4ff" }, strip);
  const gaugeHold = el("rect", { x: gauge.x, y: gauge.y + 2, width: 0, height: gauge.h - 4, fill: C.request, opacity: 0.45 }, strip);
  const gaugeCap = el("rect", { x: gauge.x, y: gauge.y - 5, width: 3, height: gauge.h + 10, fill: C.hoop }, strip);
  const gaugeFlash = el("rect", { x: gauge.x - 3, y: gauge.y - 3, width: gauge.w + 6, height: gauge.h + 6, rx: 4, fill: "none", stroke: C.parked, "stroke-width": 2, opacity: 0 }, strip);
  text(strip, gauge.x, 714, "spent", { fill: C.dim, "font-size": 10 });
  text(strip, gauge.x + 40, 714, "held before the call", { fill: C.request, "font-size": 10, opacity: 0.8 });
  text(strip, gauge.x + 150, 714, "cap", { fill: C.hoop, "font-size": 10 });

  // ---------------------------------------------------------------- lights

  const lightLayer = el("g");
  const pool = [];
  for (let i = 0; i < 10; i++) {
    const g = el("g", { opacity: 0 }, lightLayer);
    const tail = [0.22, 0.4, 0.62].map((o) => el("circle", { r: 3, opacity: o, fill: "#fff" }, g));
    const head = el("circle", { r: 5.5, fill: "#fff", filter: "url(#glow)" }, g);
    pool.push({ g, tail, head });
  }

  function place(light, name, f, color, size) {
    const w = W[name];
    const point = (x) => w.path.getPointAtLength(clamp(x, 0, 1) * w.length);
    const p = point(f);
    light.g.setAttribute("opacity", 1);
    light.head.setAttribute("cx", p.x);
    light.head.setAttribute("cy", p.y);
    light.head.setAttribute("fill", color);
    light.head.setAttribute("r", 5.5 * (size || 1));
    light.tail.forEach((c, n) => {
      const q = point(f - (3 - n) * 0.035 * Math.sign(f - (light.from ?? 0) || 1));
      c.setAttribute("cx", q.x);
      c.setAttribute("cy", q.y);
      c.setAttribute("fill", color);
    });
  }

  // ---------------------------------------------------------------- the timeline

  const events = TAPE.events;
  const plan = [];
  const arrivals = [[], [], []];
  let DURATION = 0;
  {
    let t = INTRO;
    const open = [{}, {}, {}];
    for (let i = 0; i < events.length; i++) {
      const e = events[i];
      const s = e.s || 0;
      if (e.act !== undefined) t += 0.4;
      const p = { e, i, t0: t, lights: [], roll: 0.14, dur: TICK };
      const brainWire = "brain" + s;
      const handsWire = "hands" + s;
      const send = (wire, back, color, at, size) => p.lights.push({ wire, back, color, at: at === undefined ? t : at, size });
      const arrive = (who, when) => arrivals[who].push(when);
      switch (e.k) {
        case "message":
        case "steer":
        case "answer":
          send("person", false, C.person);
          arrive(0, t + TRAVEL);
          p.dur = TRAVEL + 0.1;
          p.roll = TRAVEL;
          break;
        case "raise":
          send("person", false, C.person, t, 1.3);
          arrive(0, t + TRAVEL);
          p.dur = TRAVEL + 0.6;
          p.roll = TRAVEL + 0.35;
          break;
        case "task":
          send("sub" + s, false, C.task);
          arrive(s, t + TRAVEL);
          p.dur = TRAVEL + 0.04;
          p.roll = TRAVEL;
          break;
        case "report":
          send("sub" + e.from, true, C.report);
          arrive(0, t + TRAVEL);
          p.dur = TRAVEL + 0.05;
          p.roll = TRAVEL;
          break;
        case "model_request":
          send(brainWire, false, C.request);
          open[s].request = p;
          p.dur = TRAVEL + 0.03;
          p.roll = 0.1;
          break;
        case "model_response":
          for (let b = 0; b < 3; b++) send(brainWire, true, C.response, t + b * BURST, b ? 0.8 : 1);
          arrive(s, t + TRAVEL + 2 * BURST);
          if (open[s].request) open[s].request.answered = t + TRAVEL + 2 * BURST;
          p.dur = TRAVEL + 2 * BURST + 0.03;
          p.roll = TRAVEL + 2 * BURST;
          break;
        case "tool_request":
          if (HANDS.has(e.tool)) {
            send(handsWire, false, C.tool);
            open[s].tool = p;
            p.typing = t + TRAVEL;
            p.dur = TRAVEL;
          } else {
            p.pulse = [s, t];
          }
          break;
        case "tool_response":
          if (HANDS.has(e.tool)) {
            const asked = open[s].tool;
            p.typingFrom = asked ? asked.typing : t;
            p.typingTo = t + TYPING;
            p.shows = t;
            send(handsWire, true, C.result, t + TYPING);
            arrive(s, t + TYPING + TRAVEL);
            p.dur = TYPING + TRAVEL + 0.02;
            p.roll = TYPING + TRAVEL;
          } else if (e.tool === "ask_person") {
            send("person", true, C.person);
            p.question = t + TRAVEL;
            p.dur = TRAVEL + 0.12;
          } else {
            p.pulse = [s, t];
          }
          break;
        case "parked":
          p.dur = e.reason === "spend_budget" ? 1.0 : e.reason === "person" ? 0.45 : 0.3;
          break;
        case "loop_ended":
          p.dur = 0.2;
          break;
        case "takeover":
          p.dur = 1.15;
          p.roll = 1.0;
          break;
        case "refused":
          send(handsWire === "hands0" ? "hands0" : handsWire, true, C.refused, t, 1.35);
          p.bounce = true;
          p.dur = TRAVEL + 0.3 + 0.7;
          break;
        default:
          p.pulse = [s, t];
      }
      p.t1 = t + p.dur;
      plan.push(p);
      t = p.t1;
    }
    DURATION = t + CLOSE;
  }

  const questions = plan.filter((p) => p.question !== undefined);
  const answers = plan.filter((p) => p.e.k === "answer");
  const spoken = plan.filter((p) => p.e.k === "model_response" && p.e.text);
  const personSays = plan.filter((p) => ["message", "steer", "answer", "raise"].includes(p.e.k));
  const tools = plan.filter((p) => p.shows !== undefined);
  const acts = plan.filter((p) => p.e.act !== undefined);
  const holds = plan.filter((p) => p.e.k === "model_request" && p.e.hold);
  const parks = plan.filter((p) => p.e.k === "parked" && p.e.reason === "spend_budget");
  const raises = plan.filter((p) => p.e.k === "raise");
  const takeovers = plan.filter((p) => p.e.k === "takeover");
  const refusals = plan.filter((p) => p.e.k === "refused");
  const spawns = [null, 1, 2].map((k) => (k ? plan.find((p) => p.e.k === "task" && p.e.s === k) : null));

  function current(t) {
    let lo = 0;
    let hi = plan.length - 1;
    if (t < plan[0].t0) return -1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (plan[mid].t0 <= t) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }

  function latest(list, t, key) {
    let found = null;
    for (const p of list) if ((key ? p[key] : p.t0) <= t) found = p;
    return found;
  }

  // ---------------------------------------------------------------- the screen's lines

  function effectLines(p) {
    const e = p.e;
    const asked = e.input || {};
    const fx = e.effect || {};
    const request = plan.slice(0, p.i).reverse().find((q) => q.e.k === "tool_request" && q.e.s === e.s);
    const input = (request && request.e.input) || asked;
    if (e.tool === "read_file") {
      const lines = String(fx.text || "").replace(/\n$/, "").split("\n");
      return { title: `read_file  ${fx.path || input.path || ""}`, lines: lines.flatMap((l) => wrap(l, 40)).map((l) => [l, C.ink]) };
    }
    if (e.tool === "run_check") {
      const ok = fx.passed === true;
      return { title: "run_check", lines: wrap((ok ? "✓ " : "✗ ") + (fx.detail || ""), 40).map((w) => [w, ok ? C.good : C.bad]) };
    }
    if (e.tool === "edit_file") {
      const out = [];
      for (const l of String(input.old || "").replace(/\n$/, "").split("\n")) out.push(...hard("- " + l, 40).map((w) => [w, C.bad]));
      for (const l of String(input.new || "").replace(/\n$/, "").split("\n")) out.push(...hard("+ " + l, 40).map((w) => [w, C.good]));
      out.push([e.recovered ? "found applied: the edit landed once" : "applied", e.recovered ? C.request : C.dim]);
      return { title: `edit_file  ${fx.path || input.path || ""}`, lines: out };
    }
    return { title: e.tool, lines: [] };
  }
  for (const p of tools) p.screen = effectLines(p);

  // ---------------------------------------------------------------- draw(t)

  function draw(t) {
    const idx = current(t);
    const p = idx >= 0 ? plan[idx] : null;
    const prev = idx > 0 ? plan[idx - 1].e.n : { ...events[0].n, steps: 0, epoch: events[0].n.epoch };
    const n = p ? p.e.n : prev;

    // The numbers roll from the last recorded value to this one.
    const f = p ? smooth(clamp((t - p.t0) / p.roll, 0, 1)) : 0;
    const value = (key) => (p ? lerp(prev[key] || 0, n[key] || 0, f) : 0);
    for (const [, key] of METERS) roll(meters[key], value(key));
    roll(meters.cost_micros, p ? lerp(Math.round((prev.cost_micros || 0) / 100), Math.round((n.cost_micros || 0) / 100), f) : 0);
    roll(badge, value("epoch"));
    const cap = p ? lerp(prev.cap_micros || 0, n.cap_micros || 0, f) : events[0].n.cap_micros;
    capText.textContent = `of ${money(cap, 3)} cap`;
    const cost = value("cost_micros");
    const gx = (m) => gauge.x + (gauge.w * clamp(m, 0, scale)) / scale;
    gaugeSpend.setAttribute("width", Math.max(0, gx(cost) - gauge.x));
    gaugeCap.setAttribute("x", gx(cap) - 1.5);
    gaugeOver.setAttribute("x", gx(cap));
    gaugeOver.setAttribute("width", Math.max(0, gauge.x + gauge.w - gx(cap)));
    const hold = holds.find((h) => h.t0 <= t && t < (h.answered || h.t1));
    if (hold) {
      gaugeHold.setAttribute("x", gx(cost));
      gaugeHold.setAttribute("width", Math.max(0, gx(cost + hold.e.hold) - gx(cost)));
      holdText.textContent = `hold ${money(hold.e.hold, 4)}`;
    } else {
      gaugeHold.setAttribute("width", 0);
      holdText.textContent = "";
    }
    const park = latest(parks, t);
    const raise = latest(raises, t);
    const parkedOnSpend = park && (!raise || raise.t0 < park.t0);
    gaugeFlash.setAttribute("opacity", parkedOnSpend ? 0.55 + 0.45 * Math.abs(Math.sin((t - park.t0) * 9)) : raise ? Math.max(0, 1 - (t - raise.t0 - TRAVEL) / 0.6) : 0);
    gaugeFlash.setAttribute("stroke", parkedOnSpend ? C.parked : C.person);
    gaugeCap.setAttribute("fill", parkedOnSpend ? C.parked : C.hoop);

    // The act's label.
    const act = latest(acts, t, "t0");
    if (act) {
      const a = TAPE.acts[act.e.act];
      actTitle.textContent = a.title;
      actAbout.textContent = a.about;
      const beat = clamp((act.t0 - t) / 0.5 + 1, 0, 1);
      actTitle.setAttribute("font-size", 15 + 5 * beat);
    }

    // Lights on the wires.
    let used = 0;
    for (let k = Math.max(0, idx - 3); k <= Math.min(plan.length - 1, idx + 1); k++) {
      for (const light of plan[k].lights) {
        let f = (t - light.at) / TRAVEL;
        if (plan[k].bounce) {
          if (f < 0) continue;
          const reach = 0.93;
          const back = (t - light.at - TRAVEL * reach) / 0.3;
          let pos = f >= reach ? reach - 0.18 * Math.sin(Math.PI * 0.5 * clamp(back, 0, 1)) : f;
          const fade = clamp(1 - (back - 1) / 0.6, 0, 1);
          if (fade <= 0) continue;
          const slot = pool[used++];
          slot.from = 1;
          place(slot, light.wire, 1 - pos, light.color, light.size);
          slot.g.setAttribute("opacity", fade);
          continue;
        }
        if (f < 0 || f > 1) continue;
        const slot = pool[used++];
        if (!slot) break;
        slot.from = light.back ? 1 : 0;
        place(slot, light.wire, light.back ? 1 - f : f, light.color, light.size);
      }
    }
    for (let k = used; k < pool.length; k++) pool[k].g.setAttribute("opacity", 0);
    for (const name of Object.keys(W)) {
      const lit = plan.slice(Math.max(0, idx - 3), idx + 2).some((q) => q.lights.some((l) => l.wire === name && t >= l.at && t <= l.at + TRAVEL));
      W[name].path.setAttribute("stroke", lit ? "#3a4f8c" : C.wire);
    }

    // The engines: each light that reaches one turns its rotor 120°.
    const takeover = latest(takeovers, t);
    for (let k = 0; k < 3; k++) {
      const eng = engines[k];
      let angle = 0;
      for (const a of arrivals[k]) {
        if (a > t) break;
        angle += 120 * water((t - a) / (TURN * 2.2));
      }
      eng.rotor.setAttribute("transform", `rotate(${angle.toFixed(2)})`);
      eng.ripples.forEach((r, j) => {
        const a = arrivals[k].filter((x) => x <= t).slice(-1 - j)[0];
        const age = a === undefined ? 9 : (t - a) / 0.7;
        if (age > 1) return r.setAttribute("opacity", 0);
        const rr = (k ? 6 : 12) + age * (k ? P.subs[k].r : E.r) * 0.9;
        r.setAttribute("r", rr.toFixed(1));
        r.setAttribute("opacity", (0.8 * (1 - age)).toFixed(2));
      });
      eng.feathers.forEach((fe, j) => {
        let sway = 0;
        for (const a of arrivals[k]) {
          if (a > t) break;
          const age = t - a;
          if (age < 1.6) sway += 7 * Math.exp(-age * 2.6) * Math.sin(age * 9 + j * 0.9);
        }
        fe.setAttribute("transform", `rotate(${sway.toFixed(2)})`);
      });
      const pulses = plan.slice(Math.max(0, idx - 2), idx + 1).filter((q) => q.pulse && q.pulse[0] === k);
      const pulse = pulses.length ? clamp(1 - (t - pulses[pulses.length - 1].pulse[1]) / 0.25, 0, 1) : 0;
      eng.pulse.setAttribute("opacity", (0.7 * pulse).toFixed(2));
    }

    // The engine's state, its runner, and the refused write.
    const state = p ? p.e.state[0] : "idle";
    engineState.textContent = state;
    engineState.setAttribute("fill", state.startsWith("parked") ? C.parked : state.startsWith("idle") ? C.good : C.dim);
    let dark = 1;
    engineNote.textContent = "";
    if (takeover && t < takeover.t1 + 0.6) {
      const age = t - takeover.t0;
      if (age < 0.55) {
        dark = 0.25 + 0.5 * Math.abs(Math.sin(age * 38)) * (1 - age / 0.55);
        engineNote.textContent = `runner lost mid ${takeover.e.tool}`;
      } else {
        dark = clamp(0.3 + (age - 0.55) / 0.3, 0.3, 1);
        engineNote.textContent = `a new runner takes the session · epoch ${takeover.e.to}`;
        engineNote.setAttribute("fill", C.request);
      }
      if (age < 0.55) engineNote.setAttribute("fill", C.refused);
    }
    const refusal = latest(refusals, t);
    if (refusal) {
      engineNote.textContent = `the old runner's late ${refusal.e.tool}: refused, epoch ${refusal.e.epoch}`;
      engineNote.setAttribute("fill", C.refused);
    }
    engines[0].g.setAttribute("opacity", dark.toFixed(2));
    if (parkedOnSpend || (p && state.startsWith("parked"))) engines[0].halo.setAttribute("fill", C.parked);
    else engines[0].halo.setAttribute("fill", C.request);
    engines[0].halo.setAttribute("opacity", p && state.startsWith("parked") ? 0.2 : 0);

    // The sub-agents appear when their task reaches them.
    for (const k of [1, 2]) {
      const spawn = spawns[k];
      const shown = spawn ? clamp((t - spawn.t0) / 0.3, 0, 1) : 0;
      engines[k].g.setAttribute("opacity", shown.toFixed(2));
      subLabels[k].g.setAttribute("opacity", shown.toFixed(2));
      for (const w of ["sub", "brain", "hands"]) W[w + k].path.setAttribute("opacity", (shown * 0.9).toFixed(2));
      const st = p && p.e.state[k] ? p.e.state[k] : "";
      subLabels[k].state.textContent = st;
      subLabels[k].state.setAttribute("fill", st.startsWith("idle") ? C.good : C.dim);
    }

    // The person: what they said, and the question put to them.
    const spoke = latest(personSays, t);
    const question = latest(questions, t, "question");
    const answered = question && answers.find((a) => a.t0 > question.t0 && a.t0 <= t);
    let lines = [];
    let alpha = 0;
    let edge = C.person;
    if (question && !answered && t >= question.question) {
      lines = wrap("the engine asks: " + (question.e.effect.question || ""), 34);
      alpha = clamp((t - question.question) / 0.15, 0, 1);
      edge = C.request;
    } else if (spoke && t - spoke.t0 < 2.4) {
      const kind = spoke.e.k === "steer" ? "steers: " : spoke.e.k === "answer" ? "answers: " : "";
      const words = spoke.e.k === "raise" ? `raises the cap to ${money(spoke.e.cap, 3)}` : kind + "“" + spoke.e.text + "”";
      lines = wrap(words, 34);
      alpha = clamp(Math.min((t - spoke.t0) / 0.12, (2.4 - (t - spoke.t0)) / 0.3), 0, 1);
    }
    bubble.setAttribute("opacity", alpha.toFixed(2));
    bubbleBox.setAttribute("stroke", edge);
    bubbleBox.setAttribute("height", 16 + lines.length * 16 + 6);
    bubbleBox.setAttribute("y", 226 - (16 + lines.length * 16 + 6));
    bubbleLines.forEach((node, j) => {
      node.textContent = lines[j] || "";
      node.setAttribute("y", 226 - (16 + lines.length * 16 + 6) + 21 + j * 16);
    });
    const speaking = spoke ? clamp(1 - (t - spoke.t0) / 0.5, 0, 1) : 0;
    personHalo.setAttribute("opacity", (0.25 * speaking).toFixed(2));

    // The model: it lights while it answers, and its words stay a moment.
    const asking = holds.concat(plan.filter((q) => q.e.k === "model_request" && !q.e.hold)).find((q) => q.t0 + TRAVEL <= t && t < (q.answered || q.t1) - 2 * BURST - TRAVEL + 0.05);
    const thinking = asking ? 1 : 0;
    eyes.forEach((eye) => eye.setAttribute("fill", thinking ? C.request : "#3a4a7a"));
    bulb.setAttribute("fill", thinking ? C.request : "#3a4a7a");
    brainHalo.setAttribute("opacity", (0.22 * thinking).toFixed(2));
    const words = latest(spoken, t);
    const wordsAge = words ? t - words.t0 : 9;
    const wl = words ? wrap(words.e.text, 40) : [];
    said.forEach((node, j) => {
      node.textContent = wordsAge < 2.2 ? wl[j] || "" : "";
      node.setAttribute("opacity", clamp(Math.min(wordsAge / 0.1, (2.2 - wordsAge) / 0.3), 0, 1).toFixed(2));
    });

    // The hands: the fingers move while a tool runs, and its effect shows.
    const typing = tools.some((q) => q.typingFrom <= t && t < q.typingTo);
    fingers.forEach((finger, j) => {
      const lift = typing ? Math.max(0, Math.sin(t * 34 + j * 1.7)) * 6 : 0;
      finger.setAttribute("transform", `translate(0,${(-lift).toFixed(2)})`);
    });
    keyCaps.forEach((key, j) => {
      const hit = typing && Math.sin(t * 34 + j * 2.3) > 0.93;
      key.setAttribute("fill", hit ? C.tool : "#24305a");
    });
    const shown = latest(tools, t, "shows");
    if (shown) {
      screenTitle.textContent = shown.screen.title;
      screenLines.forEach((node, j) => {
        const line = shown.screen.lines[j];
        node.textContent = line ? line[0] : "";
        node.setAttribute("fill", line ? line[1] : C.ink);
      });
      screenFlash.setAttribute("opacity", clamp(1 - (t - shown.shows) / 0.3, 0, 1).toFixed(2));
    } else {
      screenTitle.textContent = "the workspace";
      screenLines.forEach((node) => (node.textContent = ""));
      screenFlash.setAttribute("opacity", 0);
    }
  }

  // ---------------------------------------------------------------- the clock

  let playing = !location.hash.includes("still");
  let started = null;
  let frame = 0;
  function tick(now) {
    if (!playing) return;
    if (started === null) started = now;
    const t = ((now - started) / 1000) % (DURATION + 1.5);
    draw(Math.min(t, DURATION));
    frame = requestAnimationFrame(tick);
  }

  window.dreamcatcher = {
    duration: DURATION,
    moments: () => plan.map((p) => ({ kind: p.e.k, s: p.e.s || 0, t: p.t0, end: p.t1 })),
    seek(t) {
      playing = false;
      cancelAnimationFrame(frame);
      draw(t);
    },
  };
  draw(0);
  if (playing) frame = requestAnimationFrame(tick);
})();
