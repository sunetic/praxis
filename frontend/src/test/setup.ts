import "@testing-library/jest-dom"
import { vi } from "vitest"

if (!HTMLElement.prototype.scrollIntoView) {
  HTMLElement.prototype.scrollIntoView = vi.fn()
}
if (!HTMLElement.prototype.scrollTo) {
  HTMLElement.prototype.scrollTo = vi.fn()
}

// Radix UI primitives require pointer capture APIs not available in jsdom
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false)
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = vi.fn()
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = vi.fn()
}

if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

// jsdom does not implement ResizeObserver, but responsive UI components use it
// to react to container size changes. Tests only need the observer lifecycle;
// individual tests can replace this mock when they need to trigger callbacks.
if (!globalThis.ResizeObserver) {
  class ResizeObserverMock {
    observe = vi.fn()
    unobserve = vi.fn()
    disconnect = vi.fn()
  }

  vi.stubGlobal("ResizeObserver", ResizeObserverMock)
}
