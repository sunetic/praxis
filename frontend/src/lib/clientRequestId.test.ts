import { describe, expect, it } from "vitest"

import { createClientRequestId } from "./clientRequestId"


describe("createClientRequestId", () => {
  it("uses randomUUID when the secure-context API is available", () => {
    const cryptoApi = {
      randomUUID: () => "native-request-id" as `${string}-${string}-${string}-${string}-${string}`,
      getRandomValues: <T extends ArrayBufferView | null>(values: T) => values,
    }

    expect(createClientRequestId(cryptoApi)).toBe("native-request-id")
  })

  it("creates an RFC 4122-shaped ID when randomUUID is unavailable", () => {
    const cryptoApi = {
      getRandomValues: <T extends ArrayBufferView | null>(values: T) => {
        if (values instanceof Uint8Array) values.fill(0)
        return values
      },
    }

    expect(createClientRequestId(cryptoApi)).toBe("00000000-0000-4000-8000-000000000000")
  })
})
