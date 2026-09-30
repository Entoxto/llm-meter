import { app, BrowserWindow } from "electron"
import { createReadStream, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs"
import { join, resolve } from "node:path"
import { NodeHttpClient } from "@effect/platform-node"
import { OpenCode } from "@opencode/client/effect"
import { Effect, ManagedRuntime, Stream } from "effect"
import { HttpClient, HttpClientRequest } from "effect/unstable/http"
import { createBrowserPane } from "../vendor/opencode/packages/desktop/src/main/browser-pane"
import { setBrowserEventSink } from "../vendor/opencode/packages/desktop/src/main/ipc-events"

type Bootstrap = { url: string; password: string; project: string; data_dir: string; parent_pid?: number }
type Command = { type: "focus"; session_id: string } | { type: "stop" }
type BrowserState = { type?: string; state?: { focusedTabID?: string | null; tabs?: unknown[] }; tabID?: string; error?: string }
type Binding = { id: string; session: string; attached: boolean; attaching: boolean; timer?: NodeJS.Timeout; focus?: string | null }

let config: Bootstrap | undefined
let window: BrowserWindow | undefined
let pane: ReturnType<typeof createBrowserPane> | undefined
let runtime: ReturnType<typeof ManagedRuntime.make> | undefined
let client: Awaited<ReturnType<typeof makeClient>> | undefined
let stopped = false
let firstReady = false
let active: string | undefined
const bindings = new Map<string, Binding>()
const readySessions = new Set<string>()
const blocked = new Set<string>()
const retries = new Map<string, number>()
// Electron's process.stdin wrapper reports EOF immediately for Windows pipes.
// A fresh stream over fd 0 reads the inherited pipe correctly.
const input = createReadStream(null, { fd: 0, autoClose: false })
let pendingInput = ""

function output(value: Record<string, unknown>) {
  process.stdout.write(JSON.stringify(value) + "\n")
}
function error(code: string) {
  // Never echo URLs, response bodies, provider messages, or credentials.
  output({ type: "error", code })
}
function warning(code: string, session?: string) { output({ type: "warning", code, ...(session ? { session_id: session } : {}) }) }
function folder() { return resolve(config!.data_dir) }
function privateWrite(file: string, value: string) {
  mkdirSync(folder(), { recursive: true, mode: 0o700 })
  const temp = file + "." + process.pid + ".tmp"
  writeFileSync(temp, value, { encoding: "utf8", mode: 0o600 })
  renameSync(temp, file)
}
function drop(session: string, reason?: string) {
  const binding = bindings.get(session)
  if (binding) {
    if (binding.timer) clearTimeout(binding.timer)
    bindings.delete(session)
  }
  if (readySessions.delete(session)) {
    output({ type: "detached", session_id: session, reason: reason || "closed" })
  }
}
function validBootstrap(value: unknown): value is Bootstrap {
  if (!value || typeof value !== "object") return false
  const item = value as Record<string, unknown>
  if (typeof item.url !== "string" || typeof item.password !== "string" ||
      typeof item.project !== "string" || typeof item.data_dir !== "string") return false
  if (!URL.canParse(item.url)) return false
  const url = new URL(item.url)
  return url.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) &&
    !url.username && !url.password && url.pathname === "/" && !url.search && !url.hash &&
    item.project.length > 0 && item.data_dir.length > 0
}
function sameProject(directory: string) {
  if (!config) return false
  const norm = (text: string) => resolve(text).replaceAll("\\", "/").replace(/\/+$/, "").toLowerCase()
  return norm(directory) === norm(config.project)
}
function restoreStore() {
  const file = join(folder(), "browser-tabs.json")
  let values: Record<string, Record<string, string>> = {}
  try { values = JSON.parse(readFileSync(file, "utf8")) } catch { /* first run */ }
  const save = () => privateWrite(file, JSON.stringify(values))
  return {
    get(namespace: string, key: string) { return values[namespace]?.[key] },
    set(namespace: string, key: string, value: string) {
      values[namespace] ??= {}
      values[namespace][key] = value
      save()
    },
    delete(namespace: string, key: string) {
      delete values[namespace]?.[key]
      save()
    },
  }
}
async function makeClient() {
  runtime = ManagedRuntime.make(NodeHttpClient.layerNodeHttp)
  const http = await runtime.runPromise(HttpClient.HttpClient)
  const authorization = "Basic " + Buffer.from("opencode:" + config!.password).toString("base64")
  return runtime.runPromise(OpenCode.make({ baseUrl: config!.url }).pipe(
    Effect.provideService(HttpClient.HttpClient, HttpClient.mapRequest(http, HttpClientRequest.setHeader("authorization", authorization))),
  ))
}
function present(bindingID: string, tabID: string) {
  if (!window || !pane) return
  if (active && active !== bindingID) {
    try { pane.layout(window, active) } catch { /* prior binding closed */ }
  }
  active = bindingID
  const width = Math.max(450, window.getContentBounds().width)
  const height = Math.max(350, window.getContentBounds().height)
  try {
    pane.layout(window, bindingID, {
      tabID, visible: true,
      bounds: { x: 0, y: 0, width, height },
      background: "#0d141e", radius: 0,
    } as never)
    if (!window.isVisible()) window.show()
    window.focus()
  } catch { warning("layout_failed", bindingID) }
}
function onPaneEvent(message: { bindingID: string; event: unknown }) {
  const event = message.event as BrowserState
  const session = message.bindingID
  if (event.type === "focus") {
    if (readySessions.has(session) && event.tabID) present(session, event.tabID)
    return
  }
  if (event.type !== "state") return
  if (event.error) {
    if (event.error === "browser.pane.replaced" || event.error === "browser.pane.unsupported") blocked.add(session)
    drop(session, event.error)
    if (blocked.has(session)) return
    schedule(session)
    return
  }
  const binding = bindings.get(session)
  const focus = event.state?.focusedTabID ?? null
  if (binding) binding.focus = focus
  // Inventory changes and restored tabs should not steal focus from the app.
  if (!focus && active === session && window) window.hide()
}
function schedule(session: string) {
  if (blocked.has(session) || stopped) return
  const binding = bindings.get(session)
  if (binding?.timer || binding?.attaching) return
  const attempts = retries.get(session) ?? 0
  if (attempts >= 6) {
    blocked.add(session)
    if (!firstReady) error("attachment_retry_exhausted")
    else warning("attachment_retry_exhausted", session)
    return
  }
  const delay = Math.min(10_000, 500 * 2 ** attempts)
  const next = binding ?? { id: session, session, attached: false, attaching: false }
  retries.set(session, attempts + 1)
  next.timer = setTimeout(() => { next.timer = undefined; void attach(session) }, delay)
  bindings.set(session, next)
}
async function attach(session: string) {
  if (stopped || !pane || !window || !config || readySessions.has(session) || blocked.has(session)) return
  const binding = bindings.get(session) ?? { id: session, session, attached: false, attaching: false }
  if (binding.attaching) return
  binding.attaching = true
  bindings.set(session, binding)
  try {
    await pane.register(window, binding.id, {
      serverKey: "model-studio:" + resolve(config.project).replaceAll("\\", "/").toLowerCase(),
      endpoint: { url: config.url, username: "opencode", password: config.password },
      sessionID: session,
    } as never)
    if (stopped) return
    binding.attached = true
    binding.attaching = false
    retries.delete(session)
    readySessions.add(session)
    output({ type: "attached", session_id: session })
    if (!firstReady) {
      firstReady = true
      output({ type: "ready", session_id: session })
    }
  } catch {
    binding.attaching = false
    bindings.delete(session)
    if (!stopped) {
      warning("attachment_failed", session)
      schedule(session)
    }
  }
}
async function listProjectSessions() {
  if (!client || !runtime || !config) return [] as string[]
  const found: string[] = []
  let cursor: string | undefined
  for (let page = 0; page < 10; page++) {
    const result = await runtime.runPromise(client.session.list({ directory: config.project as never, limit: 100, order: "desc", ...(cursor ? { cursor: cursor as never } : {}) }))
    for (const session of result.data) if (sameProject(session.location.directory)) found.push(session.id)
    cursor = result.cursor.next
    if (!cursor) break
  }
  return found
}
async function rescan() {
  if (stopped || !client || !runtime) return
  try {
    const sessions = await listProjectSessions()
    for (const session of sessions) if (!readySessions.has(session) && !bindings.has(session) && !blocked.has(session)) void attach(session)
  } catch { warning("session_scan_failed") }
}
async function listen() {
  if (!client || !runtime) return
  try {
    await runtime.runPromise(Stream.runForEach(client.event.subscribe(), (event) =>
      Effect.sync(() => {
        if (event.type !== "session.created" || !sameProject(event.data.location.directory)) return
        void attach(event.data.sessionID)
      }),
    ))
  } catch {
    if (!stopped) { warning("event_stream_lost"); setTimeout(() => void listen(), 2000) }
  }
}
async function start() {
  if (!config) return
  try {
    mkdirSync(join(folder(), "electron"), { recursive: true, mode: 0o700 })
    app.setPath("userData", join(folder(), "electron"))
    await app.whenReady()
    if (stopped) return
    window = new BrowserWindow({
      width: 1080, height: 720, show: false, title: "Model Studio Browser",
      webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true },
    })
    await window.loadURL("about:blank")
    pane = createBrowserPane(restoreStore() as never)
    setBrowserEventSink(onPaneEvent)
    client = await makeClient()
    await runtime!.runPromise(client.server.info())
    let sessions = await listProjectSessions()
    if (!sessions.length) {
      const created = await runtime!.runPromise(client.session.create({ location: { directory: config.project as never } }))
      sessions = [created.id]
    }
    void listen()
    for (const session of sessions) void attach(session)
    setInterval(() => void rescan(), 5000).unref()
    if (config.parent_pid) setInterval(() => {
      try { process.kill(config!.parent_pid!, 0) } catch { void stop() }
    }, 2000).unref()
  } catch {
    error("startup_failed")
    void stop()
  }
}
async function stop() {
  if (stopped) return
  stopped = true
  readySessions.clear()
  try { await pane?.dispose() } catch { /* teardown */ }
  try { await runtime?.dispose() } catch { /* teardown */ }
  window?.destroy()
  app.quit()
}
function handleLine(line: string) {
  let value: unknown
  try { value = JSON.parse(line) } catch { error("invalid_input"); return }
  if (!config) {
    if (!validBootstrap(value)) { error("invalid_bootstrap"); void stop(); return }
    config = value
    void start()
    return
  }
  const command = value as Command
  if (command.type === "stop") void stop()
  if (command.type === "focus") {
    const session = bindings.get(command.session_id)
    if (session && active === session.id && window) { window.show(); window.focus() }
  }
}
input.on("data", (data: Buffer) => {
  pendingInput += data.toString("utf8")
  if (pendingInput.length > 1024 * 1024) { error("input_too_large"); void stop(); return }
  let index: number
  while ((index = pendingInput.indexOf("\n")) >= 0) {
    const line = pendingInput.slice(0, index).replace(/\r$/, "")
    pendingInput = pendingInput.slice(index + 1)
    handleLine(line)
  }
})
input.on("end", () => void stop())
input.on("error", () => void stop())
app.on("browser-window-created", (_event, created) => {
  created.on("close", (event) => {
    if (!stopped) { event.preventDefault(); created.hide() }
  })
})
