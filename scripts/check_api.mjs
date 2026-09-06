import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import openapiTS, { astToString, COMMENT_HEADER } from "openapi-typescript";
import prettier from "prettier";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const python = path.join(
  root,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
// 再生成はメモリ内だけ。未追跡・欠落した生成物も検出し、作業中のファイルを上書きしない。
const schema = JSON.parse(
  execFileSync(
    python,
    [
      "-c",
      "import json,sys; sys.path.insert(0,'apps'); from world.api import create_app; print(json.dumps(create_app().openapi()))",
    ],
    { cwd: root, encoding: "utf8" },
  ),
);
const outputs = [
  // 生成コマンドと同じ改行・CLIヘッダーを使い、書式だけの差を検出しない。
  ["docs/api/openapi.json", JSON.stringify(schema, null, 2) + "\n"],
  ["apps/web/src/api/schema.d.ts", COMMENT_HEADER + astToString(await openapiTS(schema))],
];
let failed = false;
for (const [relative, source] of outputs) {
  const target = path.join(root, relative);
  const config = await prettier.resolveConfig(target);
  const expected = await prettier.format(source, { ...config, filepath: target });
  let actual;
  try {
    actual = await readFile(target, "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  if (actual?.replaceAll("\r\n", "\n") !== expected) {
    console.error(
      `${relative}: 生成物が欠落または古くなっています。npm run api:generate を実行してください。`,
    );
    failed = true;
  }
}
if (!failed) console.log("OpenAPI / TypeScript: 生成物は実装と一致しています。");
process.exitCode = failed ? 1 : 0;
