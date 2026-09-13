import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["demos/resident-move/web/src/**/*.test.ts"],
    isolate: true,
  },
});
