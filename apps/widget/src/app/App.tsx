import { useEffect, useState } from "preact/hooks";

import { closeWidget, readParams } from "./params";

interface Config {
  org_name: string;
  welcome_message: string;
  color: string;
}

const params = readParams();

export function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const color = config?.color ?? params.color;

  useEffect(() => {
    if (!params.apiUrl) return;
    fetch(`${params.apiUrl}/api/v1/widget/config`, {
      headers: { "X-Public-Key": params.publicKey },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => setConfig(data))
      .catch(() => undefined);
  }, []);

  return (
    <div class="app">
      <header class="header" style={{ background: color }}>
        <span class="title" data-testid="widget-title">
          {config?.org_name ?? "HelpDeck"}
        </span>
        <button class="close" aria-label="Close chat" onClick={closeWidget}>
          &#10005;
        </button>
      </header>
      <main class="body">
        <p class="welcome" data-testid="widget-welcome">
          {config?.welcome_message ?? "Hi! How can I help you today?"}
        </p>
      </main>
    </div>
  );
}
