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
 * (reiner Name ohne Trenner), die unveraendert zurueckgegeben werden. */
export function memberDisplayName(member: string): string {
  const idx = member.indexOf(MEMBER_SEP);
  return idx === -1 ? member : member.slice(idx + MEMBER_SEP.length);
}
