# The Forge

Real-time 3D digital twin of an AI data center. Single-page WebGL2 app — no build step, no node_modules.

## Run

```bash
python forge/server.py
```

Auto-opens `http://127.0.0.1:8765/index.html`. To skip the browser launch: `--no-open`.

Requires a browser with WebGL2 (Chrome, Firefox, Safari 17+, Edge).

## What's in here

- **Ground-floor data center**, multi-volume buildings, real DC equipment on the floor: server racks (12 kW std + 45 kW AI), BESS containers, air-cooled chillers, pad-mount transformers, standby gensets. Phase-accurate counts — what you see is what gets built.
- **WebGL2 renderer** with PBR-lite lighting, two-lobe IBL approximation, and ACES filmic tonemap.
- **Cinematic intro**: a water-drop depth-scan that ripples through the racks and the background, reading the actual scene depth buffer.
- **Physics-grounded sim**: closed-form CRAH / chiller / UPS / BESS model, diurnal outdoor air, free-cooling fraction by OAT, PUE derived end-to-end.
- **Site picker**: US (Ashburn, Quincy, Phoenix, Dallas, Atlanta, Hillsboro, Omaha) + Canada (Montréal, Toronto, Calgary, Vancouver, Winnipeg, Halifax). Each carries its own grid carbon, retail price, climate, and water cost.
- **Build persistence**: localStorage-backed. Dashboard and Control Room are gated until a build is committed — the Forge will not let you operate a fleet that doesn't exist on the floor.
- **Lazy startup**: Python launcher does no heavy lifting until first request, and the renderer initializes shaders on demand.

## Layout

```
forge/
├── index.html
├── css/style.css
├── js/
│   ├── main.js          entry, frame loop, input
│   ├── renderer.js      WebGL2 renderer + post composite
│   ├── shaders.js       all GLSL (geom, sky, post)
│   ├── scene.js         multi-volume building + equipment placement
│   ├── physics.js       sim (IT/cooling/BESS/PUE)
│   ├── sites.js         US + Canadian site catalog
│   ├── persistence.js   localStorage build records
│   ├── ui.js            sidebar panels, gating, HUD
│   ├── gl.js            WebGL2 helpers (program, VAO, FBO)
│   └── math.js          mat4 / vec3
└── server.py            static launcher with auto-open
```

## Camera controls

- Mouse drag: orbit
- Scroll: zoom
- Auto-orbits gently when idle
