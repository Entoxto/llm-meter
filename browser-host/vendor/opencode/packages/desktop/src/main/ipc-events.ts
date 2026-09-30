// Host adapter. The official BrowserPaneEvent is delivered to our window controller.
let sink: ((event: { bindingID: string; event: unknown }) => void) | undefined
export function setBrowserEventSink(value: typeof sink) { sink = value }
export function emitIpcEvent(_contents: unknown, value: { bindingID: string; event: unknown }) { sink?.(value) }
