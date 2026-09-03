#!/usr/bin/env node
// Rendert docs/DEPLOYMENT.md zu frontend/public/docs/deployment.html bei
// jedem Frontend-Build (siehe package.json "prebuild" -- laeuft dadurch
// automatisch sowohl im Erst-Build (entrypoint.sh) als auch bei jedem
// Auto-Update (updater.sh), da beide nur "npm run build" aufrufen).
//
// Hintergrund: die frueher haendisch als eigenstaendige HTML-Kopie
// gepflegte Version dieser Seite (frontend/public/docs/deployment.html)
// wurde nach ihrer Einfuehrung nie wieder mit Aenderungen an
// docs/DEPLOYMENT.md abgeglichen -- Aenderungen an der Markdown-Datei
// blieben in der GUI unsichtbar. Dieses Skript macht docs/DEPLOYMENT.md
// zur alleinigen Quelle; das Seiten-Grundgeruest (Kopf/CSS/Sidebar-Rahmen,
// docs-template.html) bleibt weiterhin von Hand gepflegt, da es sich kaum
// aendert.
//
// Layout-Entscheidungen, die bewusst vom generischen Markdown-Standard
// abweichen (um dem Look der urspruenglich handgeschriebenen Seite nahe zu
// bleiben), sind unten bei den jeweiligen Renderer-Overrides kommentiert.
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { marked } from "marked";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(__dirname, "../../docs/DEPLOYMENT.md");
const TEMPLATE = path.resolve(__dirname, "docs-template.html");
const DEST = path.resolve(__dirname, "../public/docs/deployment.html");

const LANG_LABELS = { powershell: "PowerShell", bash: "Bash" };

// Ab welcher Abschnittsnummer in der Seitenleiste eine neue
// Gruppenueberschrift eingefuegt wird -- rein kosmetisch (analog zur
// vorherigen Handpflege), hat keine Entsprechung in der Markdown-Struktur
// selbst und muss nur gepflegt werden, wenn eine neue grosse Phase
// dazukommt.
const GROUP_DIVIDERS = { 1: "Einrichtung", 7: "Netzwerk & Betrieb" };

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function slugify(text) {
  return (
    text
      .toLowerCase()
      .replace(/[äöüß]/g, (c) => ({ ä: "ae", ö: "oe", ü: "ue", ß: "ss" })[c])
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/(^-+|-+$)/g, "") || "section"
  );
}

function stripTags(html) {
  return html.replace(/<[^>]+>/g, "");
}

const raw = readFileSync(SRC, "utf8");
const lines = raw.split("\n");
if (!lines[0].startsWith("# ")) {
  throw new Error(`${SRC}: erste Zeile ist keine H1-Ueberschrift ("# ...")`);
}
const title = lines[0].slice(2).trim();
const firstH2Index = lines.findIndex((l) => l.startsWith("## "));
if (firstH2Index === -1) {
  throw new Error(`${SRC}: keine "## "-Ueberschrift gefunden`);
}
const introMd = lines.slice(1, firstH2Index).join("\n").trim();
const bodyMd = lines.slice(firstH2Index).join("\n");

const sections = [];
let firstHeading = true;

const renderer = new marked.Renderer();

// Jede "## "-Ueberschrift eroeffnet eine neue <section>, die vorherige wird
// geschlossen (Schluss-Tag der letzten Section nach dem Parse-Lauf unten
// manuell angehaengt, da der Renderer sie nie "von aussen" sieht). Eine
// fuehrende Nummerierung ("## 1. Titel") wird zum <span class="step-num">
// im h2 UND zur Sidebar-Nummer, exakt wie im vorher handgeschriebenen HTML.
renderer.heading = (text, level, raw) => {
  if (level === 3) return `<h3>${text}</h3>\n`;
  if (level !== 2) return `<h${level}>${text}</h${level}>\n`;

  const prefix = firstHeading ? "" : "</section>\n";
  firstHeading = false;

  const numbered = /^(\d+)\.\s+([\s\S]*)$/.exec(text);
  if (numbered) {
    const [, num, rest] = numbered;
    const label = stripTags(rest);
    if (GROUP_DIVIDERS[num]) sections.push({ divider: GROUP_DIVIDERS[num] });
    sections.push({ id: `s${num}`, num, label });
    return `${prefix}<section id="s${num}">\n<h2><span class="step-num">${num}</span>${rest}</h2>\n`;
  }

  const id = raw === "Architekturüberblick" ? "overview" : slugify(raw);
  sections.push({ id, num: null, label: stripTags(text) });
  return `${prefix}<section id="${id}">\n<h2>${text}</h2>\n`;
};

// Codebloecke: Sprachlabel aus dem Fence-Info-String (z.B. "powershell"),
// wie im Original per <span class="code-label"> ueber dem <pre>.
renderer.code = (code, infostring) => {
  const lang = (infostring || "").trim().split(/\s+/)[0].toLowerCase();
  const label = LANG_LABELS[lang] ?? lang;
  const labelHtml = label ? `<span class="code-label">${escapeHtml(label)}</span>\n` : "";
  return `<div class="code-block">\n${labelHtml}<pre><code>${escapeHtml(code)}</code></pre>\n</div>\n`;
};

// Callouts: es gibt in DEPLOYMENT.md keine eigene Markdown-Syntax dafuer --
// als Konvention gilt ein Blockquote, dessen erster Absatz mit einem fetten
// Label beginnt ("> **Label:** Text..."), analog zu den zwei bereits
// bestehenden Stellen in der Datei. Enthaelt das Label ein Signalwort wie
// "Wichtig"/"Achtung"/"Stolperstein", wird die auffaelligere "warn"-Variante
// verwendet (gelber statt gruener Rand) -- sonst die neutrale Variante.
renderer.blockquote = (quote) => {
  const match = /^<p><strong>([\s\S]+?):?<\/strong>\s*/.exec(quote);
  if (!match) return `<div class="callout">\n${quote}</div>\n`;
  const label = match[1];
  const rest = quote.slice(match[0].length);
  const warn = /wichtig|achtung|stolperstein|vorsicht/i.test(label);
  return `<div class="callout${warn ? " warn" : ""}">\n<span class="callout-label">${label}</span>\n<p>${rest}</div>\n`;
};

// Relative Verweise auf andere Markdown-Dateien im Repo (z.B. INSTALL.md)
// haben auf dieser statisch gerenderten Seite kein gueltiges Ziel -- nur
// docs/DEPLOYMENT.md selbst wird veroeffentlicht. Im rohen Markdown bleibt
// es ein echter, auf GitHub/im Editor funktionierender Link; hier wird
// daraus stattdessen ein reiner <code>-Verweis (wie im ehemals
// handgeschriebenen HTML bereits gehandhabt).
renderer.link = (href, _title, text) => {
  if (!/^https?:\/\//i.test(href) && /\.md(#.*)?$/i.test(href)) {
    return `<code>${escapeHtml(text)}</code>`;
  }
  const safeHref = escapeHtml(href);
  return `<a href="${safeHref}">${text}</a>`;
};

marked.use({ renderer, gfm: true });

const bodyHtml = marked.parse(bodyMd) + "</section>\n";
const introHtml = marked.parse(introMd);

const navParts = sections.map((s) => {
  if (s.divider) return `      <div class="group-label">${escapeHtml(s.divider)}</div>`;
  const numSpan = s.num !== null ? `<span class="num">${s.num}</span>` : "";
  return `      <a href="#${s.id}">${numSpan}${escapeHtml(s.label)}</a>`;
});
const navHtml = navParts.join("\n");

const template = readFileSync(TEMPLATE, "utf8");
const output = template
  .replace(/\{\{TITLE\}\}/g, escapeHtml(title))
  .replace("{{EYEBROW}}", "Installationsanleitung")
  .replace("{{NAV}}", navHtml)
  .replace("{{INTRO}}", introHtml)
  .replace("{{BODY}}", bodyHtml);

writeFileSync(DEST, output);
console.log(`[render-docs] ${path.relative(process.cwd(), SRC)} -> ${path.relative(process.cwd(), DEST)} (${sections.filter((s) => !s.divider).length} Abschnitte)`);
