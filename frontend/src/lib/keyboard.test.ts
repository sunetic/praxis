import { describe, expect, it } from "vitest"

import { isImeComposing, shouldSubmitOnEnter } from "@/lib/keyboard"

function createEvent(
  overrides: Partial<{ key: string; shiftKey: boolean; nativeEvent: Event }> = {},
): { key: string; shiftKey: boolean; nativeEvent: Event } {
  return {
    key: "Enter",
    shiftKey: false,
    nativeEvent: {} as Event,
    ...overrides,
  }
}

describe("isImeComposing", () => {
  it("detects native isComposing", () => {
    const event = createEvent({ nativeEvent: { isComposing: true } as unknown as Event })
    expect(isImeComposing(event)).toBe(true)
  })

  it("detects keyCode 229", () => {
    const event = createEvent({ nativeEvent: { keyCode: 229 } as unknown as Event })
    expect(isImeComposing(event)).toBe(true)
  })

  it("detects Process key", () => {
    const event = createEvent({ key: "Process" })
    expect(isImeComposing(event)).toBe(true)
  })
})

describe("shouldSubmitOnEnter", () => {
  it("allows plain Enter", () => {
    expect(shouldSubmitOnEnter(createEvent())).toBe(true)
  })

  it("blocks Shift + Enter by default", () => {
    expect(shouldSubmitOnEnter(createEvent({ shiftKey: true }))).toBe(false)
  })

  it("blocks when composing is true in options", () => {
    expect(shouldSubmitOnEnter(createEvent(), { composing: true })).toBe(false)
  })

  it("blocks IME composition native events", () => {
    expect(shouldSubmitOnEnter(createEvent({ nativeEvent: { isComposing: true } as unknown as Event }))).toBe(false)
    expect(shouldSubmitOnEnter(createEvent({ nativeEvent: { keyCode: 229 } as unknown as Event }))).toBe(false)
    expect(shouldSubmitOnEnter(createEvent({ key: "Process" }))).toBe(false)
  })
})
