import "@testing-library/jest-dom/vitest";
import "fake-indexeddb/auto";

// Polyfills jsdom doesn't ship with.
if (!global.EventSource) {
  global.EventSource = class {
    constructor() { this.readyState = 0; }
    addEventListener() {}
    removeEventListener() {}
    close() {}
  };
}
