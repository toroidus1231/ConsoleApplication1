// Site picker — US + Canadian DC-relevant cities.
// Each site contributes an environment palette (cool/warm sky), a grid mix,
// and ambient temperature climatology that the physics sim consumes.
//
// Values are deliberately approximate — the point is that climate, grid
// carbon intensity, and water cost all change visibly when the user moves.

export const SITES = [
  // ---------- United States ----------
  {
    id: "us-vaprince", country: "US", city: "Ashburn, VA",
    region: "Loudoun County · Data Center Alley",
    grid: { kind: "PJM", carbonGCO2PerKWh: 380, retailUSDPerKWh: 0.082 },
    climate: { tDbAvgC: 13, tDbDesignC: 34, humidityPct: 65 },
    waterUSDPerKgal: 7.4,
    palette: { skyTop:[0.40,0.55,0.78], skyHorizon:[0.78,0.82,0.88], sun:[3.2,3.0,2.5] },
  },
  {
    id: "us-quincywa", country: "US", city: "Quincy, WA",
    region: "Columbia Basin · hydropower",
    grid: { kind: "BPA", carbonGCO2PerKWh: 95, retailUSDPerKWh: 0.046 },
    climate: { tDbAvgC: 11, tDbDesignC: 36, humidityPct: 38 },
    waterUSDPerKgal: 4.1,
    palette: { skyTop:[0.32,0.55,0.95], skyHorizon:[0.85,0.88,0.92], sun:[3.6,3.2,2.6] },
  },
  {
    id: "us-omahane", country: "US", city: "Omaha, NE",
    region: "MISO · plains wind",
    grid: { kind: "MISO", carbonGCO2PerKWh: 460, retailUSDPerKWh: 0.071 },
    climate: { tDbAvgC: 11, tDbDesignC: 35, humidityPct: 67 },
    waterUSDPerKgal: 5.1,
    palette: { skyTop:[0.45,0.58,0.85], skyHorizon:[0.82,0.86,0.90], sun:[3.4,3.1,2.5] },
  },
  {
    id: "us-phxaz", country: "US", city: "Phoenix, AZ",
    region: "APS · solar-heavy",
    grid: { kind: "WECC-AZ", carbonGCO2PerKWh: 360, retailUSDPerKWh: 0.090 },
    climate: { tDbAvgC: 24, tDbDesignC: 43, humidityPct: 32 },
    waterUSDPerKgal: 9.2,
    palette: { skyTop:[0.55,0.60,0.78], skyHorizon:[0.95,0.85,0.62], sun:[4.0,3.4,2.5] },
  },
  {
    id: "us-dfwtx", country: "US", city: "Dallas, TX",
    region: "ERCOT · gas + wind",
    grid: { kind: "ERCOT", carbonGCO2PerKWh: 410, retailUSDPerKWh: 0.077 },
    climate: { tDbAvgC: 19, tDbDesignC: 39, humidityPct: 58 },
    waterUSDPerKgal: 6.8,
    palette: { skyTop:[0.42,0.58,0.85], skyHorizon:[0.88,0.85,0.78], sun:[3.6,3.2,2.6] },
  },
  {
    id: "us-atlga", country: "US", city: "Atlanta, GA",
    region: "Southern Co · nuclear + gas",
    grid: { kind: "SERC", carbonGCO2PerKWh: 360, retailUSDPerKWh: 0.084 },
    climate: { tDbAvgC: 17, tDbDesignC: 35, humidityPct: 72 },
    waterUSDPerKgal: 6.4,
    palette: { skyTop:[0.42,0.55,0.80], skyHorizon:[0.78,0.84,0.86], sun:[3.4,3.1,2.5] },
  },
  {
    id: "us-portland-or", country: "US", city: "Hillsboro, OR",
    region: "BPA · marine cool",
    grid: { kind: "BPA", carbonGCO2PerKWh: 130, retailUSDPerKWh: 0.069 },
    climate: { tDbAvgC: 12, tDbDesignC: 31, humidityPct: 76 },
    waterUSDPerKgal: 5.9,
    palette: { skyTop:[0.30,0.46,0.65], skyHorizon:[0.72,0.78,0.82], sun:[2.8,2.7,2.4] },
  },

  // ---------- Canada ----------
  {
    id: "ca-montrealqc", country: "CA", city: "Montréal, QC",
    region: "Hydro-Québec · hydro-dominant",
    grid: { kind: "HQ", carbonGCO2PerKWh: 30, retailUSDPerKWh: 0.052 },
    climate: { tDbAvgC: 7, tDbDesignC: 30, humidityPct: 70 },
    waterUSDPerKgal: 3.8,
    palette: { skyTop:[0.32,0.50,0.78], skyHorizon:[0.78,0.84,0.90], sun:[3.0,2.9,2.5] },
  },
  {
    id: "ca-torontoon", country: "CA", city: "Toronto, ON",
    region: "IESO · nuclear + hydro",
    grid: { kind: "IESO", carbonGCO2PerKWh: 45, retailUSDPerKWh: 0.090 },
    climate: { tDbAvgC: 9, tDbDesignC: 31, humidityPct: 71 },
    waterUSDPerKgal: 4.3,
    palette: { skyTop:[0.36,0.52,0.78], skyHorizon:[0.80,0.84,0.88], sun:[3.0,2.9,2.5] },
  },
  {
    id: "ca-calgaryab", country: "CA", city: "Calgary, AB",
    region: "AESO · gas + wind",
    grid: { kind: "AESO", carbonGCO2PerKWh: 510, retailUSDPerKWh: 0.085 },
    climate: { tDbAvgC: 4, tDbDesignC: 28, humidityPct: 55 },
    waterUSDPerKgal: 5.2,
    palette: { skyTop:[0.40,0.58,0.92], skyHorizon:[0.92,0.92,0.96], sun:[3.3,3.0,2.6] },
  },
  {
    id: "ca-vancouverbc", country: "CA", city: "Vancouver, BC",
    region: "BC Hydro · hydro-dominant",
    grid: { kind: "BCH", carbonGCO2PerKWh: 35, retailUSDPerKWh: 0.078 },
    climate: { tDbAvgC: 11, tDbDesignC: 27, humidityPct: 79 },
    waterUSDPerKgal: 4.0,
    palette: { skyTop:[0.30,0.45,0.62], skyHorizon:[0.74,0.80,0.84], sun:[2.7,2.7,2.4] },
  },
  {
    id: "ca-winnipegmb", country: "CA", city: "Winnipeg, MB",
    region: "Manitoba Hydro · hydro",
    grid: { kind: "MH", carbonGCO2PerKWh: 12, retailUSDPerKWh: 0.072 },
    climate: { tDbAvgC: 3, tDbDesignC: 30, humidityPct: 65 },
    waterUSDPerKgal: 3.6,
    palette: { skyTop:[0.36,0.55,0.86], skyHorizon:[0.84,0.88,0.93], sun:[3.0,2.9,2.5] },
  },
  {
    id: "ca-halifaxns", country: "CA", city: "Halifax, NS",
    region: "NSPI · maritime",
    grid: { kind: "NSPI", carbonGCO2PerKWh: 600, retailUSDPerKWh: 0.110 },
    climate: { tDbAvgC: 7, tDbDesignC: 26, humidityPct: 78 },
    waterUSDPerKgal: 5.4,
    palette: { skyTop:[0.30,0.48,0.72], skyHorizon:[0.76,0.80,0.86], sun:[2.8,2.8,2.4] },
  },
];

export function defaultSite() { return SITES[0]; }

export function findSite(id) {
  return SITES.find(s => s.id === id) || defaultSite();
}
