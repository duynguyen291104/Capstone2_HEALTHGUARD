import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const root = fileURLToPath(new URL("../", import.meta.url));

export function loadTs(relativePath, globals = {}) {
  const cache = new Map();
  function load(filename) {
    const resolved = path.resolve(filename);
    if (cache.has(resolved)) return cache.get(resolved);
    const loadedModule = { exports: {} };
    cache.set(resolved, loadedModule.exports);
    const compiled = ts.transpileModule(readFileSync(resolved, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const context = {
      module: loadedModule, exports: loadedModule.exports, crypto: webcrypto, URL,
      ...globals,
      require(specifier) {
        const target = specifier.startsWith("@/")
          ? path.join(root, specifier.slice(2))
          : path.resolve(path.dirname(resolved), specifier);
        return load(`${target}.ts`);
      },
    };
    vm.runInNewContext(compiled, context, { filename: resolved });
    return loadedModule.exports;
  }
  return load(path.join(root, relativePath));
}
