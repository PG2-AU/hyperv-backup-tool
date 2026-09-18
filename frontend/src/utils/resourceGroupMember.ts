import { formatSmbShareKey } from "@/utils/format";

/** ResourceGroup.members speichert VM-/CSV-Zugehoerigkeit cluster-
 * qualifiziert ("<cluster_id>::<name>") statt als reinen Namen -- zwei
 * verschiedene Hyper-V-Cluster koennen (und tun das in der Praxis) ein CSV
 * oder eine VM mit identischem Namen haben (z.B. beide "CSV01"), ein reiner
 * Name wuerde dann stillschweigend das falsche Objekt treffen (live
 * beobachteter Bug, siehe Backlog). Siehe app.models.resource_group
 * (Backend-Gegenstueck) fuer make_member_key/parse_member_key. */
const MEMBER_SEP = "::";

export function makeMemberKey(clusterId: string, name: string): string {
  return `${clusterId}${MEMBER_SEP}${name}`;
}

/** Liefert nur den Namensanteil eines Member-Eintrags -- fuer Anzeige-Zwecke
 * (Tabellen/Listen), bei denen der Cluster-Kontext nicht extra ausgeschrieben
 * werden muss. Funktioniert auch fuer noch nicht migrierte Alt-Eintraege
 * (reiner Name ohne Trenner), die unveraendert zurueckgegeben werden.
 *
 * Bei SMB_SHARE-Scope (Backlog #22) ist der Namensanteil selbst ein
 * zusammengesetzter "server|share"-Schluessel (siehe _smb_share_key in
 * jobs.py -- bewusst "|" statt "::", um keine Verwechslung mit MEMBER_SEP
 * zu erzeugen). "|" kommt in einem CSV-/VM-Namen nie vor (kein gueltiges
 * Zeichen fuer Windows-Ressourcennamen), daher reicht die reine Praesenz
 * als Signal, ohne den Scope der Resource Group hier extra durchreichen zu
 * muessen. Formatiert als UNC-Pfad, exakt wie Inventory > SMB3-Freigaben
 * (sonst zeigte die Protection-Group-Objektliste den rohen Schluessel
 * "DEMO7|vol_hv1_smb3" statt "\\DEMO7\vol_hv1_smb3" -- live gefunden
 * 2026-09-18). */
export function memberDisplayName(member: string): string {
  const idx = member.indexOf(MEMBER_SEP);
  const name = idx === -1 ? member : member.slice(idx + MEMBER_SEP.length);
  return formatSmbShareKey(name);
}
