/**
 * Game Dev Tools Panel
 * Automated playtest cycles, gallery integration, project status.
 * Appears under Tools > Game Dev in the Odysseus sidebar.
 */

(function () {
  "use strict";

  const PANEL_ID = "gamedev-panel";
  let refreshTimer = null;
  let activeCycleId = null;

  // ── DOM helpers ──────────────────────────────────────────────────────────

  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "className") e.className = v;
      else if (k === "innerHTML") e.innerHTML = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v);
    }
    for (const c of children) {
      if (typeof c === "string") e.appendChild(document.createTextNode(c));
      else if (c) e.appendChild(c);
    }
    return e;
  }

  // ── API ──────────────────────────────────────────────────────────────────

  async function api(method, path, body) {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  }

  const GET = (p) => api("GET", p);
  const POST = (p, b) => api("POST", p, b);

  // ── Render ───────────────────────────────────────────────────────────────

  function statusBadge(ok, label) {
    return el("span", {
      className: `gamedev-badge ${ok ? "ok" : "fail"}`,
      innerHTML: ok ? "&#10003; " + label : "&#10007; " + label,
    });
  }

  async function render(container) {
    container.innerHTML = "";
    container.className = "gamedev-panel";

    // Header
    container.appendChild(el("div", { className: "gamedev-header" },
      el("h2", {}, "Game Dev Tools"),
      el("p", { className: "gamedev-subtitle" }, "Chimera Unreal Engine — Automated QA")
    ));

    // ── Section 1: Quick Actions ──
    const actionsRow = el("div", { className: "gamedev-actions" });
    container.appendChild(el("h3", {}, "Quick Actions"));
    container.appendChild(actionsRow);

    // ── Section 2: Cycle Runner ──
    container.appendChild(el("h3", {}, "Run Test Cycle"));

    const runnerRow = el("div", { className: "gamedev-runner" });

    const scenarioSelect = el("select", { id: "gamedev-scenario" });
    const scenarios = ["FullFlight", "Combat", "OrbitalInsertion", "AtmosphericEntry", "FreeFlight"];
    for (const s of scenarios) {
      scenarioSelect.appendChild(el("option", { value: s }, s.replace(/([A-Z])/g, " $1").trim()));
    }

    const runBtn = el("button", {
      className: "gamedev-btn run",
      id: "gamedev-run-btn",
      innerHTML: "&#9654; Run Cycle",
      onClick: triggerCycle,
    });

    const continuousChk = el("input", { type: "checkbox", id: "gamedev-continuous" });
    const continuousLabel = el("label", {}, continuousChk, " Continuous (until pass)");

    runnerRow.appendChild(scenarioSelect);
    runnerRow.appendChild(runBtn);
    runnerRow.appendChild(continuousLabel);
    container.appendChild(runnerRow);

    // ── Section 3: Live Status ──
    container.appendChild(el("h3", {}, "Active Cycle"));
    const statusBox = el("div", { className: "gamedev-status", id: "gamedev-status" });
    statusBox.innerHTML = '<em>No active cycle. Click "Run Cycle" to start.</em>';
    container.appendChild(statusBox);

    // ── Section 4: Recent Cycles ──
    container.appendChild(el("h3", {}, "Recent Cycles"));
    const cyclesBox = el("div", { className: "gamedev-cycles", id: "gamedev-cycles" });
    cyclesBox.innerHTML = "<em>Loading...</em>";
    container.appendChild(cyclesBox);

    // ── Section 5: Project Status ──
    container.appendChild(el("h3", {}, "Project Status"));
    const projectBox = el("div", { className: "gamedev-project", id: "gamedev-project" });
    projectBox.innerHTML = "<em>Loading...</em>";
    container.appendChild(projectBox);

    // ── Set up quick actions ──
    actionsRow.appendChild(el("button", {
      className: "gamedev-btn",
      innerHTML: "&#128193; Open Gallery",
      onClick: () => {
        if (typeof window.openGallery === "function") window.openGallery();
        else if (typeof window.ui_control === "function") window.ui_control("open_panel", "gallery");
        else alert("Gallery is under Tools in the sidebar menu.");
      },
    }));

    actionsRow.appendChild(el("button", {
      className: "gamedev-btn",
      innerHTML: "&#128196; View GDD",
      onClick: () => {
        window.open("/api/gamedev/gdd", "_blank");
      },
    }));

    actionsRow.appendChild(el("button", {
      className: "gamedev-btn",
      innerHTML: "&#128269; Gallery List",
      onClick: () => {
        fetch("/api/gallery/library?limit=24")
          .then(r => r.json())
          .then(data => {
            const items = data.items || [];
            const preview = items.slice(0, 6).map(i =>
              `<div style="display:inline-block;margin:4px;text-align:center">
                <img src="/api/generated-image/${i.filename}" style="width:120px;height:68px;object-fit:cover;border-radius:4px" title="${i.prompt||''}">
                <br><small>${(i.prompt||'').slice(0,20)}</small>
              </div>`
            ).join("");
            const box = document.getElementById("gamedev-status");
            box.innerHTML = `<strong>Gallery (${data.total} photos):</strong><br>${preview}`;
          });
      },
    }));

    actionsRow.appendChild(el("button", {
      className: "gamedev-btn",
      innerHTML: "&#128260; Run Bridge",
      onClick: async () => {
        const btn = document.activeElement;
        btn.disabled = true;
        btn.innerHTML = "Running...";
        try {
          const r = await fetch("/api/gamedev/run-bridge", { method: "POST" });
          const d = await r.json();
          btn.innerHTML = d.ok ? "Done!" : "Failed";
        } catch (e) {
          btn.innerHTML = "Error";
        }
        setTimeout(() => { btn.disabled = false; btn.innerHTML = "&#128260; Run Bridge"; }, 2000);
      },
    }));

    // Load data
    refreshStatus();
    refreshTimer = setInterval(refreshStatus, 5000);
  }

  async function refreshStatus() {
    try {
      const data = await GET("/api/gamedev/status");

      // Project status
      const proj = document.getElementById("gamedev-project");
      if (proj && data.project) {
        const p = data.project;
        proj.innerHTML = [
          statusBadge(p.chimera_project, "Chimera Project").outerHTML,
          statusBadge(p.unreal_editor, "Unreal Editor").outerHTML,
          statusBadge(p.gdd_found, "GDD").outerHTML,
          statusBadge(p.vis_inbox, "VIS Inbox").outerHTML,
          `<br><small>Pending screenshots: ${p.pending_screenshots || 0} | Gallery: ${(data.gallery||{}).total_photos||0} photos</small>`,
        ].join(" ");
      }

      // Active cycles
      const stat = document.getElementById("gamedev-status");
      if (stat && data.active_cycles && data.active_cycles.length > 0) {
        const ac = data.active_cycles[0];
        const phaseList = (ac.phases || []).join(" → ");
        stat.innerHTML = `
          <div class="gamedev-active">
            <strong>${ac.scenario}</strong> — ${ac.status}
            <br>Cycle: ${ac.cycle_id}
            <br>Phases: ${phaseList || "starting..."}
            ${ac.passed !== undefined ? `<br>Passed: ${ac.passed ? "YES" : "NO"}` : ""}
            ${ac.log ? `<br><pre style="max-height:120px;overflow:auto;font-size:11px">${ac.log.slice(-8).join("\n")}</pre>` : ""}
          </div>`;
      } else if (stat && (!data.active_cycles || data.active_cycles.length === 0)) {
        stat.innerHTML = '<em>No active cycle. Select a scenario and click "Run Cycle".</em>';
      }

      // Recent cycles
      const cyc = document.getElementById("gamedev-cycles");
      if (cyc && data.recent_cycles) {
        if (data.recent_cycles.length === 0) {
          cyc.innerHTML = "<em>No cycles run yet.</em>";
        } else {
          cyc.innerHTML = data.recent_cycles.slice(0, 10).map(c => `
            <div class="gamedev-cycle-row">
              <span class="gamedev-cycle-scenario">${c.scenario || "?"}</span>
              <span class="gamedev-cycle-passed ${c.passed ? "pass" : "fail"}">${c.passed ? "PASS" : "FAIL"}</span>
              <span class="gamedev-cycle-id">${c.cycle_id}</span>
              <span class="gamedev-cycle-date">${(c.started_at || "").slice(0, 16)}</span>
            </div>
          `).join("");
        }
      }
    } catch (e) {
      // Panel not visible or server not ready — ignore
    }
  }

  async function triggerCycle() {
    const scenario = document.getElementById("gamedev-scenario").value;
    const continuous = document.getElementById("gamedev-continuous").checked;
    const btn = document.getElementById("gamedev-run-btn");

    btn.disabled = true;
    btn.innerHTML = "Starting...";

    try {
      const data = await POST("/api/gamedev/trigger", {
        scenario,
        continuous,
        max_cycles: 5,
      });
      activeCycleId = data.cycle_id;
      document.getElementById("gamedev-status").innerHTML =
        `<strong>Started!</strong> Cycle ${data.cycle_id} — ${scenario}`;
      btn.innerHTML = "&#9654; Running...";
      refreshStatus();
    } catch (e) {
      document.getElementById("gamedev-status").innerHTML =
        `<span style="color:red">Failed: ${e.message}</span>`;
    }

    setTimeout(() => {
      btn.disabled = false;
      btn.innerHTML = "&#9654; Run Cycle";
    }, 5000);
  }

  // ── Panel lifecycle ──────────────────────────────────────────────────────

  function mount(container) {
    render(container);
  }

  function unmount() {
    if (refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
  }

  // ── Register with panel system ───────────────────────────────────────────

  if (typeof window.registerPanel === "function") {
    window.registerPanel({
      id: PANEL_ID,
      label: "Game Dev",
      icon: "&#127918;",
      mount,
      unmount,
    });
  } else {
    // Fallback: hook into existing UI if panel system isn't loaded yet
    document.addEventListener("DOMContentLoaded", () => {
      // Look for a sidebar Tools section and add a button
      setTimeout(() => {
        const sidebar = document.querySelector(".sidebar-nav, .sidebar, nav");
        if (sidebar) {
          const toolsSection = sidebar.querySelector('[data-section="tools"], .tools-section');
          const btn = el("button", {
            className: "sidebar-link gamedev-link",
            innerHTML: "&#127918; Game Dev",
            onClick: () => {
              // Create a modal or find the panel area
              const main = document.querySelector(".main-content, .content, main, #content");
              if (main) {
                const existing = document.getElementById(PANEL_ID);
                if (existing) {
                  existing.style.display = existing.style.display === "none" ? "block" : "none";
                  return;
                }
                const panel = el("div", { id: PANEL_ID });
                main.prepend(panel);
                mount(panel);
              }
            },
          });
          if (toolsSection) {
            toolsSection.appendChild(btn);
          } else {
            sidebar.appendChild(btn);
          }
        }
      }, 1500);
    });
  }

  // ── CSS ──────────────────────────────────────────────────────────────────

  const style = el("style", {}, `
    .gamedev-panel { padding: 16px; font-family: -apple-system, BlinkMacSystemFont, sans-serif; color: #e0e0e0; }
    .gamedev-panel h2 { margin: 0 0 4px 0; font-size: 20px; color: #9cdef2; }
    .gamedev-panel h3 { margin: 16px 0 8px 0; font-size: 14px; color: #aaa; text-transform: uppercase; letter-spacing: 1px; }
    .gamedev-subtitle { margin: 0 0 16px 0; font-size: 12px; color: #888; }
    .gamedev-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
    .gamedev-runner { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .gamedev-runner select { padding: 6px 10px; border-radius: 4px; background: #1a1a2e; color: #e0e0e0; border: 1px solid #333; }
    .gamedev-runner label { font-size: 13px; color: #aaa; cursor: pointer; }
    .gamedev-btn { padding: 8px 14px; border-radius: 6px; border: 1px solid #444; background: #1a1a2e; color: #e0e0e0; cursor: pointer; font-size: 13px; transition: all 0.2s; }
    .gamedev-btn:hover { background: #2a2a4e; border-color: #9cdef2; }
    .gamedev-btn.run { background: #1a4a2e; border-color: #4ecdc4; color: #4ecdc4; font-weight: bold; }
    .gamedev-btn.run:hover { background: #1a6a3e; }
    .gamedev-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .gamedev-badge { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 12px; margin: 2px; }
    .gamedev-badge.ok { background: #1a3a1a; color: #4ecdc4; }
    .gamedev-badge.fail { background: #3a1a1a; color: #ff6b6b; }
    .gamedev-status { padding: 12px; border-radius: 6px; background: #111; border: 1px solid #333; min-height: 40px; margin-bottom: 8px; font-size: 13px; }
    .gamedev-cycles { max-height: 240px; overflow-y: auto; }
    .gamedev-cycle-row { display: flex; gap: 12px; padding: 4px 8px; font-size: 12px; border-bottom: 1px solid #1a1a2e; align-items: center; }
    .gamedev-cycle-row:hover { background: #1a1a2e; }
    .gamedev-cycle-scenario { flex: 1; font-weight: bold; }
    .gamedev-cycle-passed { padding: 1px 6px; border-radius: 3px; font-weight: bold; font-size: 11px; }
    .gamedev-cycle-passed.pass { background: #1a3a1a; color: #4ecdc4; }
    .gamedev-cycle-passed.fail { background: #3a1a1a; color: #ff6b6b; }
    .gamedev-cycle-id { color: #666; font-family: monospace; font-size: 11px; }
    .gamedev-cycle-date { color: #888; font-size: 11px; margin-left: auto; }
    .gamedev-active { font-size: 13px; }
    .gamedev-active pre { background: #0a0a14; padding: 8px; border-radius: 4px; color: #4ecdc4; }
  `);
  document.head.appendChild(style);

})();
