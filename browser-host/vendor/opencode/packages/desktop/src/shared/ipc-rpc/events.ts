// Host adapter for the official browser pane's event envelope.
export class BrowserPaneEvent {
  readonly bindingID: string
  readonly event: unknown
  constructor(value: { bindingID: string; event: unknown }) { this.bindingID = value.bindingID; this.event = value.event }
}
