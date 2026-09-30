# Model Studio browser companion

This is a separate Electron process for OpenCode V2's native browser. It bundles the official OpenCode desktop browser pane and Chromium implementation from [OpenCode v2.0.15, commit `6f3639d`](https://github.com/anomalyco/opencode/tree/6f3639d82ed0760091792189b78f8eeb44f699b1). The copied files retain OpenCode's MIT license in `vendor/opencode/LICENSE`. The companion adds only process lifecycle, an explicit localhost endpoint, private tab state, and a readiness gate; it does not reimplement browser tools.

## Build

On Windows with Node.js 22 or later:

```powershell
npm ci --ignore-scripts
npm run package:win
```

The package script downloads Electron if npm omitted its postinstall, builds two JavaScript bundles, and copies the Electron distribution directly into `dist/win-unpacked`. `ModelStudioBrowser.exe` is the Electron executable itself, without a launcher or self-extracting wrapper. It is owned by the launching Studio process and can be shut down by stdin EOF.

## Protocol

The first stdin line is JSON `{ "url": "http://127.0.0.1:PORT/", "password": "...", "project": "ABSOLUTE_PATH", "data_dir": "ABSOLUTE_PATH", "parent_pid": 123 }`. Credentials stay in this pipe and are never printed. Subsequent lines may be `{ "type": "focus", "session_id": "..." }` or `{ "type": "stop" }`. The companion emits newline-delimited JSON: `attached` and `detached` events with `session_id`, then `ready` after at least one official browser pane registration completes. `warning` is recoverable; `error` is fatal for the launcher. A session created in another project is ignored. The Electron window opens only on a browser tab focus request.

`dist/win-unpacked/gate` is an OpenCode plugin. Configure it with `owner_id` and `ready_file`. Its prompt hook waits up to eight seconds for an atomic lease `{ "owner_id": "...", "sessions": ["session-id"], "updated_at": 1234567890 }`, with a timestamp no older than six seconds. Studio writes the lease only after granting the corresponding session browser permission, and removes the session on detach. Without a fresh lease, the gate removes a stale Studio-marked permission and OpenCode's default browser deny rule applies. With a fresh lease, it repairs a missing Studio-owned allow rule before the prompt proceeds. It preserves following user rules, including explicit denies. The gate does not register browser tools.

## Source inventory

The 13 unmodified upstream source files and SHA-256 hashes are listed in [UPSTREAM.sha256](UPSTREAM.sha256). Paths under `vendor/opencode/packages/desktop/src/main/browser`, plus `browser-chromium.ts` and `browser-pane.ts`, are from the pinned commit. `ipc-events.ts`, `service/sidecar-credentials.ts`, and `shared/ipc-rpc/events.ts` in that tree are local adapters, not upstream copies. The OpenCode npm dependencies are pinned to 2.0.15 in `package-lock.json`.
