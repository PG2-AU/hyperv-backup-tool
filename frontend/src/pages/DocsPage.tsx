// Zeigt die Architektur-Dokumentation direkt im Content-Bereich der App
// statt sie in einem neuen Tab zu oeffnen. Die Doku selbst bleibt eine
// eigenstaendige, statische HTML-Datei (frontend/public/docs/architecture.html
// -- landet unveraendert im Vite-Build, von nginx direkt ausgeliefert), damit
// es nur eine Quelle fuer den Inhalt gibt; hier wird sie nur eingebettet.
export function DocsPage() {
  return (
    <div style={{ margin: "-16px", height: "calc(100vh - 60px)" }}>
      <iframe
        src="/docs/architecture.html"
        title="Architektur-Dokumentation"
        style={{ width: "100%", height: "100%", border: "none", display: "block" }}
      />
    </div>
  );
}
