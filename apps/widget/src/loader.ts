/**
 * HelpDeck widget loader (helpdeck.js).
 *
 * Tiny, dependency-free. Injects a launcher bubble and an isolated iframe that
 * carries the chat app. The iframe URL is derived from this script's own src so
 * one <script> tag is all a host page needs.
 */

interface LauncherConfig {
  publicKey: string;
  color: string;
  position: "bottom-right" | "bottom-left";
  appUrl: string;
  apiUrl: string;
}

function currentScript(): HTMLScriptElement | null {
  return (
    (document.currentScript as HTMLScriptElement | null) ??
    document.querySelector<HTMLScriptElement>("script[data-public-key]")
  );
}

function readConfig(script: HTMLScriptElement): LauncherConfig {
  const ds = script.dataset;
  const explicitApp = ds.appUrl;
  const derivedApp = script.src.replace(/[^/]*$/, "app/index.html");
  return {
    publicKey: ds.publicKey ?? "",
    color: ds.color ?? "#4f46e5",
    position: ds.position === "bottom-left" ? "bottom-left" : "bottom-right",
    appUrl: explicitApp ?? derivedApp,
    apiUrl: ds.apiUrl ?? "",
  };
}

function edge(position: LauncherConfig["position"]): Record<string, string> {
  return position === "bottom-left" ? { left: "20px" } : { right: "20px" };
}

function assign(el: HTMLElement, styles: Record<string, string>): void {
  // Set with !important so aggressive host CSS can't restyle our launcher/frame.
  for (const [key, value] of Object.entries(styles)) {
    const prop = key.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`);
    el.style.setProperty(prop, value, "important");
  }
}

function boot(): void {
  const script = currentScript();
  if (!script) return;
  const config = readConfig(script);

  let open = false;

  const launcher = document.createElement("button");
  launcher.setAttribute("aria-label", "Open chat");
  launcher.setAttribute("data-helpdeck", "launcher");
  launcher.innerHTML = "&#128172;"; // speech balloon
  assign(launcher, {
    position: "fixed",
    bottom: "20px",
    ...edge(config.position),
    width: "56px",
    height: "56px",
    borderRadius: "9999px",
    border: "none",
    cursor: "pointer",
    background: config.color,
    color: "#fff",
    fontSize: "24px",
    lineHeight: "56px",
    boxShadow: "0 4px 12px rgba(0,0,0,0.2)",
    zIndex: "2147483000",
  });

  const iframe = document.createElement("iframe");
  iframe.setAttribute("title", "HelpDeck chat");
  iframe.setAttribute("data-helpdeck", "frame");
  const params = new URLSearchParams({
    key: config.publicKey,
    color: config.color,
    api: config.apiUrl,
  });
  const frameSrc = `${config.appUrl}?${params.toString()}`;
  let loaded = false;
  assign(iframe, {
    position: "fixed",
    bottom: "88px",
    ...edge(config.position),
    width: "384px",
    maxWidth: "calc(100vw - 40px)",
    height: "560px",
    maxHeight: "calc(100vh - 120px)",
    border: "none",
    borderRadius: "16px",
    boxShadow: "0 12px 32px rgba(0,0,0,0.24)",
    background: "#fff",
    zIndex: "2147483000",
    display: "none",
    colorScheme: "normal",
  });

  const setOpen = (next: boolean): void => {
    open = next;
    if (open && !loaded) {
      // Lazy-load the app on first open so the host page load is untouched.
      iframe.src = frameSrc;
      loaded = true;
    }
    iframe.style.setProperty("display", open ? "block" : "none", "important");
    launcher.setAttribute("aria-label", open ? "Close chat" : "Open chat");
    launcher.innerHTML = open ? "&#10005;" : "&#128172;";
  };

  launcher.addEventListener("click", () => setOpen(!open));

  window.addEventListener("message", (event: MessageEvent) => {
    if (event.source !== iframe.contentWindow) return;
    if (event.data && event.data.type === "helpdeck:close") setOpen(false);
  });

  document.body.appendChild(launcher);
  document.body.appendChild(iframe);

  (window as unknown as { HelpDeck: unknown }).HelpDeck = {
    open: () => setOpen(true),
    close: () => setOpen(false),
    toggle: () => setOpen(!open),
  };
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
