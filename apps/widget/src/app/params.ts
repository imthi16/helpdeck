export interface WidgetParams {
  publicKey: string;
  color: string;
  apiUrl: string;
}

export function readParams(): WidgetParams {
  const params = new URLSearchParams(window.location.search);
  return {
    publicKey: params.get("key") ?? "",
    color: params.get("color") ?? "#4f46e5",
    apiUrl: params.get("api") ?? "",
  };
}

export function closeWidget(): void {
  window.parent.postMessage({ type: "helpdeck:close" }, "*");
}
