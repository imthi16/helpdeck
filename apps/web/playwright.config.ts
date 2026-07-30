import { defineConfig, devices } from "@playwright/test";

const WEB_PORT = 3020;
const API_PORT = 8020;
const WEB_URL = `http://localhost:${WEB_PORT}`;
const API_URL = `http://localhost:${API_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: WEB_URL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // Run the arq ingest worker (background) next to the API so uploads reach `ready`.
      command:
        `uv run alembic upgrade head && ` +
        `(uv run arq app.workers.main.WorkerSettings & ` +
        `uv run uvicorn app.main:app --port ${API_PORT})`,
      cwd: "../api",
      port: API_PORT,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        ALLOWED_ORIGINS: WEB_URL,
        STORAGE_DIR: ".e2e-storage",
        // Invite links must point at the test dashboard, not the dev one.
        WEB_APP_URL: WEB_URL,
        // Pin the AI stack to the offline stubs, exactly as tests/conftest.py does.
        // These specs assert on stub behaviour ("the offline model stub can ground
        // an answer here"), so a provider key left in apps/api/.env would make them
        // issue real, billed, rate-limited calls and assert against a live model.
        // An empty OLLAMA_BASE_URL reads as unreachable.
        OLLAMA_BASE_URL: "",
        ANTHROPIC_API_KEY: "",
        OPENAI_API_KEY: "",
        GEMINI_API_KEY: "",
      },
    },
    {
      command: `pnpm exec next dev --port ${WEB_PORT}`,
      port: WEB_PORT,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_API_URL: API_URL,
      },
    },
  ],
});
