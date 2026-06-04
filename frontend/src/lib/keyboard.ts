type EnterSubmitEvent = {
  key: string
  shiftKey: boolean
  nativeEvent: Event
}

type NativeKeyboardEvent = KeyboardEvent & {
  isComposing?: boolean
  keyCode?: number
}

export function isImeComposing(event: Pick<EnterSubmitEvent, "key" | "nativeEvent">): boolean {
  const native = event.nativeEvent as NativeKeyboardEvent
  return event.key === "Process" || native.isComposing === true || native.keyCode === 229
}

export function shouldSubmitOnEnter(
  event: EnterSubmitEvent,
  options: { allowShiftEnter?: boolean; composing?: boolean } = {},
): boolean {
  if (event.key !== "Enter") return false
  if (!options.allowShiftEnter && event.shiftKey) return false
  if (options.composing) return false
  return !isImeComposing(event)
}
