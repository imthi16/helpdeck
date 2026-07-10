import { defineConfig, devices } from "@playwright/test";

const PORT = 4173;
const API_PORT = 8030;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // Build the widget, then serve the package root so demo + dist are reachable.
      command: `pnpm build && python3 -m http.server ${PORT}`,
      port: PORT,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      // API + fixed-key widget demo org for the full chat E2E.
      command:
        "uv run alembic upgrade head && " +
        "uv run python -m scripts.seed_widget && " +
        `uv run uvicorn app.main:app --port ${API_PORT}`,
      cwd: "../api",
      port: API_PORT,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        ALLOWED_ORIGINS: `http://localhost:${PORT}`,
        STORAGE_DIR: ".e2e-widget-storage",
      },
    },
  ],
});
