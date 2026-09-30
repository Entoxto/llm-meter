import { build } from "esbuild"
import { resolve } from "node:path"

await build({
  entryPoints: ["src/app.ts"],
  outfile: "dist/app.cjs",
  bundle: true,
  platform: "node",
  target: "node22",
  format: "cjs",
  external: ["electron"],
  plugins: [{
    name: "narrow-effect-node-http",
    setup(build) {
      build.onResolve({ filter: /^@effect\/platform-node$/ }, () => ({ path: resolve("src/effect-node-http.ts") }))
      build.onResolve({ filter: /^@effect\/platform-node-shared\// }, (args) => ({
        path: resolve("node_modules/@effect/platform-node-shared/dist", args.path.slice("@effect/platform-node-shared/".length) + ".js"),
      }))
    },
  }],
  sourcemap: false,
  logLevel: "info",
})

await build({
  entryPoints: ["gate/index.ts"],
  outfile: "dist/gate/index.mjs",
  bundle: true,
  platform: "node",
  target: "node22",
  format: "esm",
  logLevel: "info",
})
