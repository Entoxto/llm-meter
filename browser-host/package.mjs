import { cpSync, existsSync, mkdirSync, renameSync, rmSync, writeFileSync } from "node:fs"
import { join, resolve, sep } from "node:path"
import { execFileSync } from "node:child_process"

const root = resolve(import.meta.dirname)
const electron = join(root, "node_modules", "electron", "dist")
if (!existsSync(join(electron, "electron.exe"))) {
  execFileSync(process.execPath, [join(root, "node_modules", "electron", "install.js")], { cwd: root, stdio: "inherit" })
}
const target = join(root, "dist", "win-unpacked")
if (resolve(target) !== resolve(root, "dist", "win-unpacked") || !resolve(target).startsWith(root + sep)) {
  throw new Error("Package output must stay inside browser-host/dist")
}
rmSync(target, { recursive: true, force: true })
cpSync(electron, target, { recursive: true })
renameSync(join(target, "electron.exe"), join(target, "ModelStudioBrowser.exe"))
const app = join(target, "resources", "app")
mkdirSync(app, { recursive: true })
cpSync(join(root, "dist", "app.cjs"), join(app, "app.cjs"))
writeFileSync(join(app, "package.json"), JSON.stringify({ name: "model-studio-browser-host", version: "0.1.0", main: "app.cjs" }))
const gate = join(target, "gate")
mkdirSync(gate, { recursive: true })
cpSync(join(root, "dist", "gate", "index.mjs"), join(gate, "index.mjs"))
writeFileSync(join(gate, "package.json"), JSON.stringify({ name: "model-studio-browser-gate", version: "0.1.0", type: "module", main: "index.mjs", exports: "./index.mjs" }))
cpSync(join(root, "vendor", "opencode", "LICENSE"), join(target, "OPENCODE-LICENSE"))
cpSync(join(root, "UPSTREAM.sha256"), join(target, "UPSTREAM.sha256"))
cpSync(join(root, "README.md"), join(target, "README.md"))
console.log(join(target, "ModelStudioBrowser.exe"))
