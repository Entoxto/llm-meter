import { test } from "node:test"
import assert from "node:assert/strict"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { Effect } from "effect"
import gate from "../dist/gate/index.mjs"

async function harness(record, lease) {
  const folder = await mkdtemp(join(tmpdir(), "studio-browser-gate-"))
  const file = join(folder, "ready.json")
  if (lease) await writeFile(file, JSON.stringify(lease))
  let hook
  const updates = []
  const ctx = {
    options: { owner_id: "owner", ready_file: file },
    session: {
      hook: (_name, callback) => Effect.sync(() => { hook = callback }),
      get: () => Effect.succeed(record),
      update: (value) => Effect.sync(() => { updates.push(value) }),
    },
  }
  await Effect.runPromise(gate.effect(ctx))
  return { file, folder, updates, run: () => Effect.runPromise(hook({ sessionID: "ses_test" })) }
}

test("fresh lease leaves attached session permissions untouched", async () => {
  const lease = { owner_id: "owner", sessions: ["ses_test"], updated_at: Date.now() / 1000 }
  const h = await harness({ permissions: [{ action: "browser", resource: "*", effect: "allow" }], metadata: { model_studio_browser_host: "owner" } }, lease)
  try { await h.run(); assert.equal(h.updates.length, 0) } finally { await rm(h.folder, { recursive: true }) }
})

test("fresh lease repairs a missed grant and preserves user deny", async () => {
  const lease = { owner_id: "owner", sessions: ["ses_test"], updated_at: Date.now() / 1000 }
  const h = await harness({ permissions: [{ action: "browser", resource: "private", effect: "deny" }], metadata: { user: "keep" } }, lease)
  try {
    await h.run()
    assert.equal(h.updates.length, 1)
    assert.deepEqual(h.updates[0].permissions, [
      { action: "browser", resource: "*", effect: "allow" },
      { action: "browser", resource: "private", effect: "deny" },
    ])
    assert.deepEqual(h.updates[0].metadata, { user: "keep", model_studio_browser_host: "owner" })
  } finally { await rm(h.folder, { recursive: true }) }
})

test("stale Studio rule is removed while user rules and metadata survive", async () => {
  const h = await harness({
    permissions: [
      { action: "browser", resource: "*", effect: "allow" },
      { action: "browser", resource: "private", effect: "deny" },
    ],
    metadata: { model_studio_browser_host: "prior-owner", user: "keep" },
  })
  try {
    const run = h.run()
    for (let i = 0; i < 40 && !h.updates.length; i++) await new Promise((resolve) => setTimeout(resolve, 25))
    assert.equal(h.updates.length, 1)
    assert.deepEqual(h.updates[0].permissions, [{ action: "browser", resource: "private", effect: "deny" }])
    assert.deepEqual(h.updates[0].metadata, { user: "keep" })
    await writeFile(h.file, JSON.stringify({ owner_id: "owner", sessions: ["ses_test"], updated_at: Date.now() / 1000 }))
    await run
  } finally { await rm(h.folder, { recursive: true }) }
})
