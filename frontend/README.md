# Commissioning Platform — Frontend

React 18 single-page app for the data-center commissioning platform (Modules
18–20 of the spec). It talks to the Python FastAPI backend over the REST + SSE
API documented in `docs/contracts.txt` §4, and is served in production as
static files from `/app/frontend/build` at `/` by the API server (Module 21).

## Stack

- **React 18.3** with hooks + Context (no Redux), `react-router-dom` 6.28.
- **Vite 5** as the dev server / bundler. Build output goes to `build/`.
- No charting or UI libraries — the dependency surface is just
  `react`, `react-dom`, `react-router-dom`.

## Requirements

- Node.js 18+ and npm (developed against Node 22 / npm 10).

## Install

```bash
cd frontend
npm install
```

## Develop

```bash
npm run dev
```

Starts Vite on http://localhost:3000 and proxies `/api` to the backend on
`http://localhost:8080` (see `vite.config.js`). On first load the app prompts
for the facility **API key** (Contracts §6.3); it is stored in `localStorage`
and sent as the `X-API-Key` header on every request.

## Build

```bash
npm run build      # emits ./build
npm run preview    # serve the built bundle locally
```

The backend mounts `build/` at `/`:

```python
app.mount("/", StaticFiles(directory="/app/frontend/build", html=True), name="frontend")
```

## Architecture

| Path | Component | Endpoints (Contracts §4) |
| --- | --- | --- |
| `/` | Dashboard (Module 18) | `GET /punchlist/summary`, `GET /system/health`, SSE |
| `/discovery` | Discovery | `POST /discovery/scan`, `GET /discovery/status\|results/:id`, SSE |
| `/devices` | DeviceList | `GET /devices`, SSE |
| `/devices/:id` | DeviceDetail | `GET /devices/:id`, `GET /devices/:id/history`, SSE |
| `/tests` | TestExecution | `GET /tests/history`, SSE |
| `/tests/:id` | TestDetail | `GET /tests/status/:id`, `GET /tests/live/:id` (SSE) |
| `/punchlist` | PunchList | `GET /punchlist`, `PATCH /punchlist/:id`, SSE |
| `/checklist` | Checklist (Module 19) | `GET /checklist/:id`, `POST /checklist/:id/:item_id` |
| `/attestation` | Attestation (Module 20) | `GET /attestation/chain\|verify\|certificate\|:hash`, export |
| `/reports` | Reports | `POST /reports/generate`, `GET /reports/:id`, reconciliation |
| `/import` | Import | `POST /bim/import` + `POST /config/generate` flows |

### Real-time (SSE, §6.2)

`FacilityContext` opens one `EventSource` to `GET /api/v1/system/events` and
fans events out via context. Routing:

- `poll_result` → live device register values (`LiveRegisters`)
- `test_started` / `test_completed` / `test_aborted` → test status
- `punch_item` → punch list (live append)
- `manual_confirmation_needed` → global confirm/deny modal
  (`POST /tests/confirm/:id` or `POST /tests/abort/:id`)
- `worker_health` → health indicator refresh
- `device_discovered` → discovery counter

### Offline checklist (§6.5)

- `public/service-worker.js` (authored in `src/serviceWorker.js`) precaches the
  app shell, serves client routes offline, and caches `GET /devices` and
  `GET /checklist/*`.
- `src/offline.js` queues submissions in **IndexedDB** when offline, compresses
  photos to 1024px / JPEG q80, and flushes the queue on reconnect (earliest
  client timestamp wins on conflict).
- Devices can be selected by scanning a `NETBOX:{device_id}` QR payload (§9.1).

## Key files

- `src/hooks/useApi.js` — fetch wrapper (GET/POST/PATCH/multipart/download) with
  `X-API-Key`.
- `src/hooks/useSSE.js` — single `EventSource`, per-type dispatch + counters.
- `src/context/FacilityContext.jsx` — global state, health, SSE, confirm queue.
- `src/components/ui.jsx` — shared `StatusPill`, `Card`, `DataTable`, states.
