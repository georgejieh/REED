import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

function demoDatasetPlugin(): Plugin {
  return {
    name: "reed-demo-dataset",
    apply: (_config, { command }) => command === "build",
    configResolved(config) {
      if (config.mode !== "demo") return;
      const raw = (config.env.VITE_DATASET_BASE ?? "") as string | undefined;
      const samplePath = resolve(config.root, "..", "data", "samples", "example-digest.json");
      if (!existsSync(samplePath)) {
        config.logger.warn(
          `reed-demo-dataset: baked sample missing at ${samplePath}; demo build will fail at first request`,
        );
      }
      if (!raw) {
        config.logger.warn(
          "reed-demo-dataset: VITE_DATASET_BASE is unset; demo build will use the baked sample only",
        );
        return;
      }
      try {
        const url = new URL(raw);
        if (url.protocol !== "https:") {
          config.logger.warn(
            `reed-demo-dataset: VITE_DATASET_BASE must be https; got ${url.protocol}; falling back to baked sample`,
          );
        }
        if (
          url.hostname === "localhost" ||
          url.hostname === "127.0.0.1" ||
          url.hostname === "::1"
        ) {
          config.logger.warn(
            "reed-demo-dataset: VITE_DATASET_BASE points at a loopback host; rejecting and falling back to baked sample",
          );
        }
      } catch {
        config.logger.warn(
          "reed-demo-dataset: VITE_DATASET_BASE is not a valid URL; falling back to baked sample",
        );
      }
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), demoDatasetPlugin()],
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
}));