import { readFile } from "node:fs/promises"
import { setTimeout as delay } from "node:timers/promises"
import { Plugin } from "@opencode/plugin/effect"
import { Effect } from "effect"

type Lease = { owner_id?: unknown; sessions?: unknown; updated_at?: unknown }

async function ready(path: string, owner: string, session: string): Promise<boolean> {
  try {
    const lease = JSON.parse(await readFile(path, "utf8")) as Lease
    const stamp = Number(lease.updated_at)
    const now = Date.now() / 1000
    return lease.owner_id === owner && Array.isArray(lease.sessions) && lease.sessions.includes(session) &&
      Number.isFinite(stamp) && stamp <= now + 1 && now - stamp <= 6
  } catch { return false }
}

export default Plugin.define({
  id: "model-studio.browser-readiness-gate",
  effect: (ctx) => {
    const owner = ctx.options.owner_id
    const file = ctx.options.ready_file
    const allow = { action: "browser", resource: "*", effect: "allow" } as const
    const isAllow = (rule: { action?: string; resource?: string; effect?: string } | undefined) =>
      rule?.action === allow.action && rule?.resource === allow.resource && rule?.effect === allow.effect
    const ensure = (sessionID: string) => Effect.gen(function* () {
      const session = yield* ctx.session.get({ sessionID })
      const marker = session.metadata?.model_studio_browser_host
      const rules = [...(session.permissions ?? [])]
      if (marker === owner && isAllow(rules[0])) return
      // Only a marked prefix is ours. Preserve all following user rules.
      if (typeof marker === "string" && marker && isAllow(rules[0])) rules.shift()
      const metadata = { ...(session.metadata ?? {}), model_studio_browser_host: owner }
      if (!(yield* Effect.promise(() => ready(file, owner, sessionID)))) return
      yield* ctx.session.update({ sessionID, permissions: [allow, ...rules], metadata })
    }).pipe(Effect.catch(() => Effect.void))
    const revoke = (sessionID: string) => Effect.gen(function* () {
      const session = yield* ctx.session.get({ sessionID })
      const marker = session.metadata?.model_studio_browser_host
      if (typeof marker !== "string" || !marker) return
      const rules = [...(session.permissions ?? [])]
      if (isAllow(rules[0])) rules.shift()
      const metadata = { ...(session.metadata ?? {}) }
      delete metadata.model_studio_browser_host
      // A concurrently attached helper may have published a valid lease while
      // the session was read. Recheck immediately before the update.
      if (typeof owner === "string" && typeof file === "string" &&
          (yield* Effect.promise(() => ready(file, owner, sessionID)))) return
      yield* ctx.session.update({ sessionID, permissions: rules, metadata })
    }).pipe(Effect.catch(() => Effect.void))
    return ctx.session.hook("prompt", (event) => Effect.gen(function* () {
      if (typeof owner !== "string" || typeof file !== "string") return
      if (yield* Effect.promise(() => ready(file, owner, event.sessionID))) { yield* ensure(event.sessionID); return }
      yield* revoke(event.sessionID)
      const deadline = Date.now() + 8000
      while (Date.now() < deadline) {
        if (yield* Effect.promise(() => ready(file, owner, event.sessionID))) { yield* ensure(event.sessionID); return }
        yield* Effect.promise(() => delay(200))
      }
      // A stale rule on a resumed/forked session must not outlive the timeout.
      if (!(yield* Effect.promise(() => ready(file, owner, event.sessionID)))) yield* revoke(event.sessionID)
    }))
  },
})
