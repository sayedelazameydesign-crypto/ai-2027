import { useMemo } from 'react';
import {
  AlertCircle,
  Boxes,
  FolderGit2,
  Layers,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { StatusBadge } from './StatusBadge';
import {
  capabilityMatrix,
  nameOf,
  notesOf,
  statusCounts,
  type CapabilityStatus,
  type Language as CapLang,
} from './capabilities';
import type { ConnectionState, Language } from './integrations';

const copyByLanguage = {
  ar: {
    title: 'المشاريع وخريطة القدرات',
    subtitle: 'فجوة القدرات الحقيقية — لا أزرار توهم الاكتمال',
    source: 'مصدر البيانات: capabilities.json · projects/ (للمرجع فقط)',
    refresh: 'تحديث',
    legend: 'شارات الحالة',
    capabilities: 'القدرات',
    skills: 'المهارات',
    projects: 'المشاريع',
    summary: 'ملخّص الصدق',
    summaryNote: 'النسبة أدناه محسوبة من خريطة القدرات فقط ولا تلمّح بقدرة غير منفّذة.',
    open: 'فتح',
    plannedHint: 'مخطّط — لا يوجد تشغيل حي بعد.',
    blockedHint: 'محظور في هذه المرحلة.',
    unavailableHint: 'غير متاح حاليًا.',
    partialHint: 'موجود جزئيًا — بعض المسارات متصلة، وأخرى لا.',
    implementedHint: 'منفّذ فعليًا ومتصل بالواجهات الحقيقية.',
    noAction: 'بلا إجراء تلقائي',
  },
  en: {
    title: 'Projects & capability map',
    subtitle: 'The real capability gap — no buttons implying completion',
    source: 'Data source: capabilities.json · projects/ (reference only)',
    refresh: 'Refresh',
    legend: 'Status legend',
    capabilities: 'Capabilities',
    skills: 'Skills',
    projects: 'Projects',
    summary: 'Honesty summary',
    summaryNote: 'The share below is computed from the capability matrix only and implies no unimplemented power.',
    open: 'Open',
    plannedHint: 'Planned — no live runtime yet.',
    blockedHint: 'Blocked in this phase.',
    unavailableHint: 'Unavailable right now.',
    partialHint: 'Partial — some paths are connected, others are not.',
    implementedHint: 'Actually implemented and connected to real interfaces.',
    noAction: 'No automatic action',
  },
} as const;

type Copy = { [Key in keyof (typeof copyByLanguage)['en']]: string };

type GroupKey = 'capabilities' | 'skills' | 'projects';

const ORDER: CapabilityStatus[] = ['implemented', 'partial', 'planned', 'unavailable', 'blocked'];

function hintFor(status: CapabilityStatus, copy: Copy): string {
  switch (status) {
    case 'implemented': return copy.implementedHint;
    case 'partial': return copy.partialHint;
    case 'planned': return copy.plannedHint;
    case 'unavailable': return copy.unavailableHint;
    default: return copy.blockedHint;
  }
}

interface ProjectsWorkspaceProps {
  lang: Language;
  onConnectionChange: (state: ConnectionState) => void;
  onOpenSection: (section: GroupKey) => void;
}

export function ProjectsWorkspace({ lang, onConnectionChange, onOpenSection }: ProjectsWorkspaceProps) {
  const copy = copyByLanguage[lang];
  // The project map is static and honest; report a stable connection state.
  useMemo(() => onConnectionChange('online'), [onConnectionChange]);

  const counts = statusCounts();
  const totalCards =
    capabilityMatrix.capabilities.length +
    capabilityMatrix.skills.length +
    capabilityMatrix.projects.length;

  const renderGroup = (items: Array<{
    id: string; name_ar: string; name_en: string; status: CapabilityStatus;
    notes_ar: string; notes_en: string; source?: string;
  }>) => (
    <div className="capability-card-grid">
      {items.map((item) => (
        <article key={item.id} className={`capability-card capability-status-${item.status}`}>
          <div className="capability-card-head">
            <strong>{nameOf(item, lang as CapLang)}</strong>
            <StatusBadge status={item.status} lang={lang as CapLang} />
          </div>
          <p className="capability-card-notes">{notesOf(item, lang as CapLang)}</p>
          <div className="capability-card-foot">
            {item.source && <code className="capability-source">{item.source}</code>}
            <small className="capability-hint">{hintFor(item.status, copy)}</small>
          </div>
        </article>
      ))}
    </div>
  );

  return (
    <div className="integration-page projects-page" dir={lang === 'ar' ? 'rtl' : 'ltr'}>
      <div className="integration-page-header">
        <div className="integration-heading">
          <div className="integration-heading-icon projects-heading-icon"><FolderGit2 size={19} /></div>
          <div>
            <div className="integration-eyebrow">{copy.source}</div>
            <h1>{copy.title}</h1>
            <p>{copy.subtitle}</p>
          </div>
        </div>
      </div>

      <section className="capability-summary studio-surface">
        <div className="studio-section-heading">
          <div><span className="section-kicker"><ShieldCheck size={13} />{copy.summary}</span><h2>{copy.summary}</h2></div>
        </div>
        <div className="capability-summary-grid">
          {ORDER.map((status) => (
            <div key={status} className={`capability-summary-cell status-${status}`}>
              <strong>{counts[status]}</strong>
              <StatusBadge status={status} lang={lang as CapLang} />
            </div>
          ))}
        </div>
        <p className="capability-summary-note"><Sparkles size={13} />{copy.summaryNote}</p>
      </section>

      <section className="capability-group">
        <div className="studio-section-heading">
          <div><span className="section-kicker"><Boxes size={13} />{copy.capabilities}</span><h2>{copy.capabilities}</h2></div>
          <button className="secondary-button" onClick={() => onOpenSection('capabilities')}>{copy.open}</button>
        </div>
        {renderGroup(capabilityMatrix.capabilities)}
      </section>

      <section className="capability-group">
        <div className="studio-section-heading">
          <div><span className="section-kicker"><Layers size={13} />{copy.skills}</span><h2>{copy.skills}</h2></div>
          <button className="secondary-button" onClick={() => onOpenSection('skills')}>{copy.open}</button>
        </div>
        {renderGroup(capabilityMatrix.skills)}
      </section>

      <section className="capability-group">
        <div className="studio-section-heading">
          <div><span className="section-kicker"><FolderGit2 size={13} />{copy.projects}</span><h2>{copy.projects}</h2></div>
          <button className="secondary-button" onClick={() => onOpenSection('projects')}>{copy.open}</button>
        </div>
        {renderGroup(capabilityMatrix.projects)}
      </section>

      <div className="capability-legend">
        <AlertCircle size={13} />
        <span>{copy.legend}:</span>
        {ORDER.map((status) => (
          <StatusBadge key={status} status={status} lang={lang as CapLang} />
        ))}
        <span className="capability-total">{totalCards}</span>
      </div>
    </div>
  );
}
