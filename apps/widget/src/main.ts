function currentScript(): HTMLScriptElement | null {
  return (
    (document.currentScript as HTMLScriptElement | null) ??
    document.querySelector<HTMLScriptElement>("script[data-public-key]")
  );
}

const script = currentScript();
const publicKey = script?.dataset.publicKey ?? null;

console.log("HelpDeck loaded", { publicKey });

export { publicKey };
