import data from './capabilities.json';

export type CapabilityStatus =
  | 'implemented'
  | 'partial'
  | 'planned'
  | 'unavailable'
  | 'blocked';

export interface CapabilityEntry {
  id: string;
  name_ar: string;
  name_en: string;
  status: CapabilityStatus;
  source: string;
  action: string | null;
  notes_ar: string;
  notes_en: string;
}

export interface SkillEntry {
  id: string;
  name_ar: string;
  name_en: string;
  status: CapabilityStatus;
  notes_ar: string;
  notes_en: string;
}

export interface ProjectEntry {
  id: string;
  name_ar: string;
  name_en: string;
  status: CapabilityStatus;
  notes_ar: string;
  notes_en: string;
}

export interface CapabilityMatrix {
  version: string;
  product: string;
  assistant: string;
  statuses: CapabilityStatus[];
  capabilities: CapabilityEntry[];
  skills: SkillEntry[];
  projects: ProjectEntry[];
}

const matrix = data as unknown as CapabilityMatrix;

export const capabilityMatrix: CapabilityMatrix = matrix;

export type Language = 'ar' | 'en';

export function statusLabel(status: CapabilityStatus, lang: Language): string {
  const labels: Record<CapabilityStatus, { ar: string; en: string }> = {
    implemented: { ar: 'منفّذ', en: 'Implemented' },
    partial: { ar: 'جزئي', en: 'Partial' },
    planned: { ar: 'مخطّط', en: 'Planned' },
    unavailable: { ar: 'غير متاح', en: 'Unavailable' },
    blocked: { ar: 'محظور', en: 'Blocked' },
  };
  return labels[status][lang];
}

export function nameOf(entry: { name_ar: string; name_en: string }, lang: Language): string {
  return lang === 'ar' ? entry.name_ar : entry.name_en;
}

export function notesOf(
  entry: { notes_ar: string; notes_en: string },
  lang: Language,
): string {
  return lang === 'ar' ? entry.notes_ar : entry.notes_en;
}

/** Capabilities whose buttons must never imply a completed, connected action. */
export function isHonestNoAction(status: CapabilityStatus): boolean {
  return status === 'planned' || status === 'unavailable' || status === 'blocked';
}

export function statusCounts(): Record<CapabilityStatus, number> {
  const counts: Record<CapabilityStatus, number> = {
    implemented: 0,
    partial: 0,
    planned: 0,
    unavailable: 0,
    blocked: 0,
  };
  for (const capability of matrix.capabilities) counts[capability.status] += 1;
  for (const skill of matrix.skills) counts[skill.status] += 1;
  for (const project of matrix.projects) counts[project.status] += 1;
  return counts;
}

export function implementedShare(): number {
  const total =
    matrix.capabilities.length + matrix.skills.length + matrix.projects.length;
  if (total === 0) return 0;
  const implemented =
    matrix.capabilities.filter((c) => c.status === 'implemented').length +
    matrix.skills.filter((s) => s.status === 'implemented').length +
    matrix.projects.filter((p) => p.status === 'implemented').length;
  return implemented / total;
}
