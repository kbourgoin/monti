import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globalSetup: ["test/fakeMonarch.ts"],
    // Every file shares one fake-monarch whose mode is global state.
    fileParallelism: false,
    testTimeout: 20_000,
  },
});
